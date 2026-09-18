from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user, render, subscription_context
from ..models import CatalogProfessor, Contact, Position, User

router = APIRouter(tags=["pages"])

# How long after a first email the dashboard suggests a follow-up.
FOLLOW_UP_AFTER_DAYS = 14


@router.get("/health")
def health(db: Session = Depends(get_db)):
    """Liveness + database reachability, for the platform's health check."""
    db.execute(select(1))
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@router.get("/")
def home(
    request: Request,
    user: User | None = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user is None:
        catalog_size = int(
            db.execute(
                select(func.count(CatalogProfessor.id)).where(
                    CatalogProfessor.is_published.is_(True),
                    CatalogProfessor.is_removed.is_(False),
                )
            ).scalar_one()
        )
        return render(request, "marketing/landing.html", {"catalog_size": catalog_size})

    counts = {
        "contacts": int(
            db.execute(
                select(func.count(Contact.id)).where(Contact.owner_id == user.id)
            ).scalar_one()
        ),
        "emailed": int(
            db.execute(
                select(func.count(Contact.id)).where(
                    Contact.owner_id == user.id, Contact.email_sent.is_(True)
                )
            ).scalar_one()
        ),
        "positions": int(
            db.execute(
                select(func.count(Position.id)).where(Position.owner_id == user.id)
            ).scalar_one()
        ),
    }
    # Emailed at least this long ago, no reminder yet: time for a polite nudge.
    cutoff = datetime.now(timezone.utc) - timedelta(days=FOLLOW_UP_AFTER_DAYS)
    follow_ups = list(
        db.execute(
            select(Contact)
            .where(
                Contact.owner_id == user.id,
                Contact.email_sent.is_(True),
                Contact.reminder_sent.is_(False),
                Contact.email_sent_at.is_not(None),
                Contact.email_sent_at <= cutoff,
            )
            .order_by(Contact.email_sent_at)
            .limit(5)
        ).scalars()
    )
    today = date.today()
    deadlines = list(
        db.execute(
            select(Position)
            .where(
                Position.owner_id == user.id,
                Position.deadline.is_not(None),
                Position.deadline >= today,
                Position.status.not_in(["rejected", "archived", "offer"]),
            )
            .order_by(Position.deadline)
            .limit(5)
        ).scalars()
    )
    return render(
        request,
        "home.html",
        {
            "counts": counts,
            "follow_ups": follow_ups,
            "deadlines": deadlines,
            "today": today,
            **subscription_context(db, user),
        },
    )


@router.get("/dashboard")
def dashboard_redirect():
    """Back-compat for links that predate the /professors route."""
    return RedirectResponse("/professors", status_code=307)
