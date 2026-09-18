from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user, render, subscription_context
from ..models import CatalogProfessor, Contact, Position, User

router = APIRouter(tags=["pages"])


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
    return render(
        request, "home.html", {"counts": counts, **subscription_context(db, user)}
    )


@router.get("/dashboard")
def dashboard_redirect():
    """Back-compat for links that predate the /professors route."""
    return RedirectResponse("/professors", status_code=307)
