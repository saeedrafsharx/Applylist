from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import render, require_verified, safe_back, set_flash, subscription_context
from ..models import Position, User
from ..schemas import PositionForm
from ..services.audit import Action, log_activity

router = APIRouter(prefix="/positions", tags=["positions"])

SORTS = {
    "field": (Position.field,),
    "link": (Position.link, Position.field),
    "category": (Position.category, Position.field),
    "status": (Position.status, Position.field),
    "deadline": (Position.deadline.asc().nullslast(), Position.field),
    "recent": (Position.created_at.desc(),),
}


def _owned(db: Session, user: User, position_id: int) -> Optional[Position]:
    position = db.get(Position, position_id)
    if position is None or position.owner_id != user.id:
        return None
    return position


def _parse_deadline(raw: str) -> Optional[date]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


@router.get("")
def positions(
    request: Request,
    q: str = "",
    sort: str = "field",
    category: str = "",
    status: str = "",
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    stmt = select(Position).where(Position.owner_id == user.id)

    categories = sorted(
        {
            row or "General"
            for row in db.execute(
                select(Position.category).where(Position.owner_id == user.id).distinct()
            ).scalars()
        }
    )

    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(Position.field.ilike(like), Position.link.ilike(like), Position.notes.ilike(like))
        )
    if category:
        stmt = stmt.where(Position.category == category)
    if status:
        stmt = stmt.where(Position.status == status)

    stmt = stmt.order_by(*SORTS.get(sort, SORTS["field"]))
    rows = list(db.execute(stmt).scalars())

    today = date.today()
    stats = {
        "total": len(rows),
        "applied": sum(1 for p in rows if p.status in {"applied", "interview", "offer"}),
        "due_soon": sum(
            1 for p in rows if p.deadline and 0 <= (p.deadline - today).days <= 14
        ),
        "overdue": sum(
            1 for p in rows
            if p.deadline and p.deadline < today and p.status == "interested"
        ),
    }

    return render(
        request,
        "positions/index.html",
        {
            "positions": rows,
            "stats": stats,
            "q": q,
            "sort": sort,
            "category": category,
            "status": status,
            "categories": categories,
            "statuses": Position.STATUSES,
            "today": today,
            **subscription_context(db, user),
        },
    )


@router.post("/add")
def add_position(
    request: Request,
    field: str = Form(...),
    link: str = Form(...),
    category: str = Form(""),
    status: str = Form("interested"),
    deadline: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    try:
        payload = PositionForm(
            field=field,
            link=link,
            category=category,
            status=status if status in Position.STATUSES else "interested",
            deadline=_parse_deadline(deadline),
            notes=notes or None,
        )
    except ValidationError as exc:
        set_flash(request, exc.errors()[0]["msg"], "error")
        return RedirectResponse("/positions", status_code=303)

    position = Position(owner_id=user.id, **payload.model_dump())
    db.add(position)
    db.flush()
    log_activity(
        db, Action.POSITION_CREATED, user_id=user.id, request=request,
        target_type="position", target_id=position.id, summary=f"Added {position.field}",
    )
    set_flash(request, "Position added.", "success")
    return RedirectResponse("/positions", status_code=303)


@router.get("/edit/{position_id}")
def edit_position_form(
    request: Request,
    position_id: int,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    position = _owned(db, user, position_id)
    if position is None:
        return RedirectResponse("/positions", status_code=303)
    return render(
        request,
        "positions/edit.html",
        {"p": position, "statuses": Position.STATUSES},
    )


@router.post("/edit/{position_id}")
def edit_position(
    request: Request,
    position_id: int,
    field: str = Form(...),
    link: str = Form(...),
    category: str = Form(""),
    status: str = Form("interested"),
    deadline: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    position = _owned(db, user, position_id)
    if position is None:
        return RedirectResponse("/positions", status_code=303)

    try:
        payload = PositionForm(
            field=field,
            link=link,
            category=category,
            status=status if status in Position.STATUSES else "interested",
            deadline=_parse_deadline(deadline),
            notes=notes or None,
        )
    except ValidationError as exc:
        return render(
            request,
            "positions/edit.html",
            {"p": position, "statuses": Position.STATUSES,
             "error": exc.errors()[0]["msg"]},
            status_code=400,
        )

    for name, value in payload.model_dump().items():
        setattr(position, name, value)

    log_activity(
        db, Action.POSITION_UPDATED, user_id=user.id, request=request,
        target_type="position", target_id=position.id, summary=f"Updated {position.field}",
    )
    set_flash(request, "Position saved.", "success")
    return RedirectResponse("/positions", status_code=303)


@router.post("/delete/{position_id}")
def delete_position(
    request: Request,
    position_id: int,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    position = _owned(db, user, position_id)
    if position is not None:
        log_activity(
            db, Action.POSITION_DELETED, user_id=user.id, request=request,
            target_type="position", target_id=position.id, summary=f"Deleted {position.field}",
        )
        db.delete(position)
        set_flash(request, "Position deleted.", "info")
    return RedirectResponse("/positions", status_code=303)


@router.post("/status/{position_id}")
def set_status(
    request: Request,
    position_id: int,
    status: str = Form(...),
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    position = _owned(db, user, position_id)
    if position is not None and status in Position.STATUSES:
        position.status = status
    return RedirectResponse(safe_back(request, "/positions"), status_code=303)
