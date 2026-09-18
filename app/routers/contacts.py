from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..i18n import _
from ..deps import render, require_verified, safe_back, set_flash, subscription_context
from ..models import Contact, User
from ..schemas import ContactForm
from ..services.audit import Action, log_activity

router = APIRouter(tags=["contacts"])

SORTS = {
    "name": (Contact.name,),
    "university": (Contact.university, Contact.name),
    "email": (Contact.contact_email, Contact.name),
    "category": (Contact.category, Contact.name),
    "recent": (Contact.created_at.desc(),),
}


def _owned(db: Session, user: User, contact_id: int) -> Optional[Contact]:
    contact = db.get(Contact, contact_id)
    if contact is None or contact.owner_id != user.id:
        return None
    return contact


@router.get("/professors")
def professors(
    request: Request,
    q: str = "",
    sort: str = "name",
    category: str = "",
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    stmt = select(Contact).where(Contact.owner_id == user.id)

    categories = sorted(
        {
            row or "General"
            for row in db.execute(
                select(Contact.category).where(Contact.owner_id == user.id).distinct()
            ).scalars()
        }
    )

    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                Contact.name.ilike(like),
                Contact.university.ilike(like),
                Contact.research_focus.ilike(like),
                Contact.contact_email.ilike(like),
                Contact.notes.ilike(like),
            )
        )
    if category:
        stmt = stmt.where(Contact.category == category)

    stmt = stmt.order_by(*SORTS.get(sort, SORTS["name"]))
    contacts = list(db.execute(stmt).scalars())

    stats = {
        "total": len(contacts),
        "sent": sum(1 for c in contacts if c.email_sent),
        "reminders": sum(1 for c in contacts if c.reminder_sent),
        "pending": sum(1 for c in contacts if not c.email_sent),
    }

    return render(
        request,
        "contacts/index.html",
        {
            "contacts": contacts,
            "stats": stats,
            "q": q,
            "sort": sort,
            "category": category,
            "categories": categories,
            "imported": request.query_params.get("imported"),
            "updated": request.query_params.get("updated"),
            "skipped": request.query_params.get("skipped"),
            **subscription_context(db, user),
        },
    )


@router.post("/add")
def add_contact(
    request: Request,
    name: str = Form(...),
    university: str = Form(...),
    research_focus: str = Form(...),
    contact_email: str = Form(...),
    source_url: str = Form(""),
    category: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    try:
        payload = ContactForm(
            name=name,
            university=university,
            research_focus=research_focus,
            contact_email=contact_email,
            source_url=source_url,
            category=category,
            notes=notes or None,
        )
    except ValidationError as exc:
        set_flash(request, exc.errors()[0]["msg"], "error")
        return RedirectResponse("/professors", status_code=303)

    contact = Contact(owner_id=user.id, **payload.model_dump())
    db.add(contact)
    db.flush()
    log_activity(
        db, Action.CONTACT_CREATED, user_id=user.id, request=request,
        target_type="contact", target_id=contact.id, summary=f"Added {contact.name}",
    )
    set_flash(request, _("Added {name}.", name=contact.name), "success")
    return RedirectResponse("/professors", status_code=303)


@router.get("/edit/{contact_id}")
def edit_form(
    request: Request,
    contact_id: int,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    contact = _owned(db, user, contact_id)
    if contact is None:
        return RedirectResponse("/professors", status_code=303)
    return render(request, "contacts/edit.html", {"c": contact})


@router.post("/edit/{contact_id}")
def edit_contact(
    request: Request,
    contact_id: int,
    name: str = Form(...),
    university: str = Form(...),
    research_focus: str = Form(...),
    contact_email: str = Form(...),
    source_url: str = Form(""),
    category: str = Form(""),
    notes: str = Form(""),
    email_sent: Optional[str] = Form(None),
    reminder_sent: Optional[str] = Form(None),
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    contact = _owned(db, user, contact_id)
    if contact is None:
        return RedirectResponse("/professors", status_code=303)

    try:
        payload = ContactForm(
            name=name,
            university=university,
            research_focus=research_focus,
            contact_email=contact_email,
            source_url=source_url,
            category=category,
            notes=notes or None,
        )
    except ValidationError as exc:
        return render(
            request,
            "contacts/edit.html",
            {"c": contact, "error": exc.errors()[0]["msg"]},
            status_code=400,
        )

    for field, value in payload.model_dump().items():
        setattr(contact, field, value)

    now_sent = email_sent is not None
    if now_sent != contact.email_sent:
        contact.email_sent = now_sent
        contact.email_sent_at = datetime.now(timezone.utc) if now_sent else None
    contact.reminder_sent = reminder_sent is not None

    log_activity(
        db, Action.CONTACT_UPDATED, user_id=user.id, request=request,
        target_type="contact", target_id=contact.id, summary=f"Updated {contact.name}",
    )
    set_flash(request, _("Saved {name}.", name=contact.name), "success")
    return RedirectResponse("/professors", status_code=303)


@router.post("/delete/{contact_id}")
def delete_contact(
    request: Request,
    contact_id: int,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    contact = _owned(db, user, contact_id)
    if contact is not None:
        log_activity(
            db, Action.CONTACT_DELETED, user_id=user.id, request=request,
            target_type="contact", target_id=contact.id, summary=f"Deleted {contact.name}",
        )
        db.delete(contact)
        set_flash(request, "Contact deleted.", "info")
    return RedirectResponse("/professors", status_code=303)


@router.post("/toggle-email/{contact_id}")
def toggle_email(
    request: Request,
    contact_id: int,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    contact = _owned(db, user, contact_id)
    if contact is not None:
        contact.email_sent = not contact.email_sent
        contact.email_sent_at = datetime.now(timezone.utc) if contact.email_sent else None
    # Back to wherever the click came from (filtered list, dashboard), not the bare list.
    return RedirectResponse(safe_back(request, "/professors"), status_code=303)


@router.post("/toggle-reminder/{contact_id}")
def toggle_reminder(
    request: Request,
    contact_id: int,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    contact = _owned(db, user, contact_id)
    if contact is not None:
        contact.reminder_sent = not contact.reminder_sent
    return RedirectResponse(safe_back(request, "/professors"), status_code=303)


# ── CSV ─────────────────────────────────────────────────────────

CSV_HEADER = [
    "Name", "University", "Research Focus", "Contact Email", "Source URL",
    "Category", "Notes", "Email Sent", "Email Sent At (UTC)", "Reminder Sent",
]


@router.get("/export.csv")
def export_csv(
    request: Request,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    contacts = list(
        db.execute(
            select(Contact)
            .where(Contact.owner_id == user.id)
            .order_by(Contact.university, Contact.name)
        ).scalars()
    )

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for c in contacts:
        writer.writerow([
            c.name, c.university, c.research_focus, c.contact_email,
            c.source_url or "", c.category or "", c.notes or "",
            "Yes" if c.email_sent else "No",
            c.email_sent_at.isoformat() if c.email_sent_at else "",
            "Yes" if c.reminder_sent else "No",
        ])

    # utf-8-sig so Excel opens non-ASCII names correctly.
    data = buffer.getvalue().encode("utf-8-sig")
    buffer.close()

    log_activity(
        db, Action.CONTACT_EXPORTED, user_id=user.id, request=request,
        summary=f"Exported {len(contacts)} contacts",
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        iter([data]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="applylist-{stamp}.csv"'},
    )


ALIASES = {
    "name": ["name", "fullname", "contactname", "professor"],
    "university": ["university", "uni", "institution", "school", "affiliation"],
    "research_focus": ["researchfocus", "researcharea", "area", "topic", "field", "research"],
    "contact_email": ["contactemail", "email", "emailaddress", "mail"],
    "source_url": ["sourceurl", "source", "link", "url", "website", "page", "profile"],
    "category": ["category", "folder", "group", "tag"],
    "notes": ["notes", "note", "comment", "comments"],
    "email_sent": ["emailsent", "sent", "emailsentflag"],
    "email_sent_at": ["emailsentat", "emailsentdate", "sentat", "emailsenton"],
    "reminder_sent": ["remindersent", "reminder", "followup", "followupsent"],
}


def _norm(value: str) -> str:
    return "".join(ch.lower() for ch in (value or "") if ch.isalnum())


def _to_bool(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "بله"}


def _parse_dt(value) -> Optional[datetime]:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _sniff_delimiter(text: str) -> str:
    for delimiter in (",", ";", "\t", "|"):
        header = next(csv.reader(io.StringIO(text), delimiter=delimiter), [])
        normed = [_norm(h) for h in header]
        known = sum(1 for h in normed if any(h in syns for syns in ALIASES.values()))
        if known >= 2:
            return delimiter
    return ","


@router.post("/import.csv")
async def import_csv(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        set_flash(request, "That file is too large (limit 5 MB).", "error")
        return RedirectResponse("/professors", status_code=303)

    text = None
    for encoding in ("utf-8-sig", "utf-8", "cp1256", "iso-8859-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "ignore")

    reader = csv.DictReader(io.StringIO(text), delimiter=_sniff_delimiter(text))
    headers = {_norm(h): h for h in (reader.fieldnames or [])}
    mapping = {
        target: headers[syn]
        for target, syns in ALIASES.items()
        for syn in syns
        if syn in headers
    }

    def value_of(row: dict, target: str) -> str:
        column = mapping.get(target)
        return (row.get(column) or "").strip() if column else ""

    created = updated = skipped = 0

    for row in reader:
        name = value_of(row, "name")
        contact_email = value_of(row, "contact_email")
        if not name or not contact_email:
            skipped += 1
            continue

        email_sent = _to_bool(value_of(row, "email_sent"))
        sent_at = _parse_dt(value_of(row, "email_sent_at"))

        existing = db.execute(
            select(Contact).where(
                Contact.owner_id == user.id,
                Contact.name == name,
                Contact.contact_email == contact_email,
            )
        ).scalars().first()

        if existing is not None:
            for field in ("university", "research_focus", "source_url", "category", "notes"):
                incoming = value_of(row, field)
                if incoming:
                    setattr(existing, field, incoming)
            existing.email_sent = email_sent
            existing.email_sent_at = sent_at if email_sent else None
            existing.reminder_sent = _to_bool(value_of(row, "reminder_sent"))
            updated += 1
        else:
            db.add(
                Contact(
                    owner_id=user.id,
                    name=name,
                    contact_email=contact_email,
                    university=value_of(row, "university"),
                    research_focus=value_of(row, "research_focus"),
                    source_url=value_of(row, "source_url") or "#",
                    category=value_of(row, "category") or "General",
                    notes=value_of(row, "notes") or None,
                    email_sent=email_sent,
                    email_sent_at=sent_at if email_sent else None,
                    reminder_sent=_to_bool(value_of(row, "reminder_sent")),
                )
            )
            created += 1

    log_activity(
        db, Action.CONTACT_IMPORTED, user_id=user.id, request=request,
        summary=f"Imported {created} new, {updated} updated, {skipped} skipped",
        detail={"created": created, "updated": updated, "skipped": skipped,
                "filename": file.filename},
    )
    return RedirectResponse(
        f"/professors?imported={created}&updated={updated}&skipped={skipped}",
        status_code=303,
    )
