from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from ..db import get_db
from ..deps import (
    get_current_user,
    render,
    require_verified,
    safe_back,
    set_flash,
    subscription_context,
)
from ..models import (
    CatalogJobTitle,
    CatalogProfessor,
    CatalogUniversity,
    Contact,
    TakedownRequest,
    User,
)
from ..services.audit import Action, log_activity
from ..services.billing import user_has_feature

router = APIRouter(prefix="/database", tags=["catalog"])

PAGE_SIZE = 25
# What a free user sees before the paywall: enough to judge whether it's worth
# paying for, with contact addresses withheld.
PREVIEW_ROWS = 6


def _visible_professors():
    return select(CatalogProfessor).where(
        CatalogProfessor.is_published.is_(True),
        CatalogProfessor.is_removed.is_(False),
    )


def mask_email(email: Optional[str]) -> str:
    """`d.bzdok@mcgill.ca` -> `d••••@mcgill.ca` for the unpaid preview."""
    if not email or "@" not in email:
        return "—"
    local, _, domain = email.partition("@")
    return f"{local[:1]}{'•' * max(3, len(local) - 1)}@{domain}"


@router.get("")
def database_home(
    request: Request,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    """Landing page: the pitch for free users, the search entry for paid ones."""
    can = user_has_feature(db, user, "catalog")

    totals = {
        "professors": db.execute(
            select(func.count()).select_from(_visible_professors().subquery())
        ).scalar_one(),
        "universities": db.execute(
            select(func.count(func.distinct(CatalogProfessor.university_id))).where(
                CatalogProfessor.is_published.is_(True),
                CatalogProfessor.is_removed.is_(False),
            )
        ).scalar_one(),
        "countries": db.execute(
            select(func.count(func.distinct(CatalogUniversity.country))).where(
                CatalogUniversity.country.is_not(None)
            )
        ).scalar_one(),
        "job_titles": db.execute(
            select(func.count(CatalogJobTitle.id)).where(
                CatalogJobTitle.is_published.is_(True)
            )
        ).scalar_one(),
    }

    preview = list(
        db.execute(
            _visible_professors()
            .options(joinedload(CatalogProfessor.university))
            .order_by(CatalogProfessor.updated_at.desc())
            .limit(PREVIEW_ROWS)
        ).scalars()
    )

    if not can:
        log_activity(
            db, Action.CATALOG_BLOCKED, user_id=user.id, request=request,
            summary="Viewed the database paywall",
        )

    return render(
        request,
        "catalog/home.html",
        {
            "can_browse": can,
            "totals": totals,
            "preview": preview,
            "mask_email": mask_email,
            **subscription_context(db, user),
        },
    )


@router.get("/professors")
def browse_professors(
    request: Request,
    q: str = "",
    university: str = "",
    country: str = "",
    page: int = 1,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    can = user_has_feature(db, user, "catalog")
    if not can:
        set_flash(request, "The contact database is part of a paid plan.", "info")
        return RedirectResponse("/database", status_code=303)

    page = max(1, page)
    stmt = _visible_professors().options(joinedload(CatalogProfessor.university))

    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                CatalogProfessor.name.ilike(like),
                CatalogProfessor.research_focus.ilike(like),
                CatalogProfessor.department.ilike(like),
                CatalogProfessor.title.ilike(like),
            )
        )
    if university:
        stmt = stmt.join(CatalogUniversity).where(CatalogUniversity.slug == university)
    elif country:
        stmt = stmt.join(CatalogUniversity).where(CatalogUniversity.country == country)

    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()

    rows = list(
        db.execute(
            stmt.order_by(CatalogProfessor.name)
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        ).scalars()
    )

    universities = list(
        db.execute(select(CatalogUniversity).order_by(CatalogUniversity.name)).scalars()
    )
    countries = [
        row
        for row in db.execute(
            select(CatalogUniversity.country)
            .where(CatalogUniversity.country.is_not(None))
            .distinct()
            .order_by(CatalogUniversity.country)
        ).scalars()
    ]

    # Which of these the user has already copied across.
    saved_ids = {
        row
        for row in db.execute(
            select(Contact.catalog_professor_id).where(
                Contact.owner_id == user.id,
                Contact.catalog_professor_id.is_not(None),
            )
        ).scalars()
    }

    log_activity(
        db, Action.CATALOG_VIEWED, user_id=user.id, request=request,
        summary=f"Searched the database for {q!r}" if q else "Browsed the database",
    )

    return render(
        request,
        "catalog/professors.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "pages": max(1, math.ceil(total / PAGE_SIZE)),
            "page_size": PAGE_SIZE,
            "q": q,
            "university": university,
            "country": country,
            "universities": universities,
            "countries": countries,
            "saved_ids": saved_ids,
            **subscription_context(db, user),
        },
    )


@router.get("/jobs")
def browse_jobs(
    request: Request,
    q: str = "",
    field: str = "",
    level: str = "",
    page: int = 1,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    can = user_has_feature(db, user, "catalog")
    if not can:
        set_flash(request, "The job-title database is part of a paid plan.", "info")
        return RedirectResponse("/database", status_code=303)

    page = max(1, page)
    stmt = select(CatalogJobTitle).where(CatalogJobTitle.is_published.is_(True))

    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                CatalogJobTitle.title.ilike(like),
                CatalogJobTitle.description.ilike(like),
                CatalogJobTitle.typical_requirements.ilike(like),
            )
        )
    if field:
        stmt = stmt.where(CatalogJobTitle.field == field)
    if level:
        stmt = stmt.where(CatalogJobTitle.level == level)

    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows = list(
        db.execute(
            stmt.order_by(CatalogJobTitle.field, CatalogJobTitle.title)
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        ).scalars()
    )

    fields = [
        row for row in db.execute(
            select(CatalogJobTitle.field)
            .where(CatalogJobTitle.field.is_not(None))
            .distinct()
            .order_by(CatalogJobTitle.field)
        ).scalars()
    ]
    levels = [
        row for row in db.execute(
            select(CatalogJobTitle.level)
            .where(CatalogJobTitle.level.is_not(None))
            .distinct()
            .order_by(CatalogJobTitle.level)
        ).scalars()
    ]

    return render(
        request,
        "catalog/jobs.html",
        {
            "rows": rows,
            "total": total,
            "page": page,
            "pages": max(1, math.ceil(total / PAGE_SIZE)),
            "q": q,
            "field": field,
            "level": level,
            "fields": fields,
            "levels": levels,
            **subscription_context(db, user),
        },
    )


@router.post("/save/{professor_id}")
def save_to_contacts(
    request: Request,
    professor_id: int,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    """Copy a catalog entry into the user's own tracked contacts."""
    if not user_has_feature(db, user, "catalog"):
        return RedirectResponse("/database", status_code=303)

    professor = db.get(CatalogProfessor, professor_id)
    if professor is None or not professor.is_visible:
        set_flash(request, "That entry is no longer available.", "error")
        return RedirectResponse("/database/professors", status_code=303)

    already = db.execute(
        select(Contact).where(
            Contact.owner_id == user.id,
            Contact.catalog_professor_id == professor.id,
        )
    ).scalar_one_or_none()
    if already is not None:
        set_flash(request, f"{professor.name} is already in your list.", "info")
        return RedirectResponse(safe_back(request, "/database/professors"), 303)

    contact = Contact(
        owner_id=user.id,
        name=professor.name,
        university=professor.university.name if professor.university else "",
        research_focus=(professor.research_focus or professor.title or "")[:500],
        contact_email=professor.email or "",
        source_url=professor.profile_url or professor.source_url or "#",
        category="From database",
        catalog_professor_id=professor.id,
    )
    db.add(contact)
    db.flush()

    log_activity(
        db, Action.CATALOG_SAVED, user_id=user.id, request=request,
        target_type="catalog_professor", target_id=professor.id,
        summary=f"Saved {professor.name} to contacts",
    )
    set_flash(request, f"Added {professor.name} to your professors list.", "success")
    return RedirectResponse(safe_back(request, "/database/professors"), 303)


# ── takedown ────────────────────────────────────────────────────


@router.get("/takedown")
def takedown_form(request: Request, professor: int = 0, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    """
    Public removal request form.

    Deliberately reachable without an account: the people listed in the catalog
    are not our users, and asking them to register before they can ask to be
    removed would be indefensible.
    """
    entry = db.get(CatalogProfessor, professor) if professor else None
    return render(request, "catalog/takedown.html", {"entry": entry})


@router.post("/takedown")
def submit_takedown(
    request: Request,
    subject_name: str = Form(...),
    requester_email: str = Form(""),
    reason: str = Form(""),
    professor_id: int = Form(0),
    db: Session = Depends(get_db),
):
    entry = db.get(CatalogProfessor, professor_id) if professor_id else None

    db.add(
        TakedownRequest(
            professor_id=entry.id if entry else None,
            subject_name=subject_name.strip()[:200],
            requester_email=(requester_email or "").strip()[:320] or None,
            reason=(reason or "").strip() or None,
            status=TakedownRequest.STATUS_OPEN,
            created_at=datetime.now(timezone.utc),
        )
    )

    # Unpublish immediately; an admin confirms or restores it afterwards.
    # Erring toward removing a real person's data while a human looks at it is
    # the right default here.
    if entry is not None:
        entry.is_published = False

    return render(request, "catalog/takedown.html", {"submitted": True})
