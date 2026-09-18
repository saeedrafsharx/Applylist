from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import Optional

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User
from .services.billing import active_subscription, user_has_feature


class AppRedirect(Exception):
    """Raised inside a dependency to bounce the user somewhere else."""

    def __init__(self, url: str, message: Optional[str] = None, level: str = "info"):
        self.url = url
        self.message = message
        self.level = level
        super().__init__(url)


# ── templates ───────────────────────────────────────────────────

templates = Jinja2Templates(directory="templates")


def _fmt_toman(rial: Optional[int]) -> str:
    if not rial:
        return "Free"
    return f"{rial // 10:,} Toman"


def _fmt_dt(value: Optional[datetime], fmt: str = "%Y-%m-%d %H:%M") -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime(fmt)


def _fmt_date(value, fmt: str = "%Y-%m-%d") -> str:
    if value is None:
        return "—"
    return value.strftime(fmt)


templates.env.filters["toman"] = _fmt_toman
templates.env.filters["dt"] = _fmt_dt
templates.env.filters["d"] = _fmt_date
templates.env.globals["settings"] = settings
templates.env.globals["now"] = lambda: datetime.now(timezone.utc)


def render(request: Request, name: str, context: Optional[dict] = None, **kwargs):
    """TemplateResponse with the per-request extras every page needs."""
    ctx = {
        "request": request,
        "user": getattr(request.state, "user", None),
        "flash": pop_flash(request),
    }
    ctx.update(context or {})
    return templates.TemplateResponse(name, ctx, **kwargs)


# ── redirects ───────────────────────────────────────────────────


def safe_back(request: Request, fallback: str) -> str:
    """
    Where to send the user after an action, honouring the page they came from.

    `Referer` is set by the client, so it is never trusted as a destination:
    only a same-site absolute path is accepted, and a protocol-relative one
    (`//evil.test`) is rejected. Anything else falls back.
    """
    referer = request.headers.get("referer") or ""
    if not referer:
        return fallback
    parsed = urlparse(referer)
    if parsed.scheme or parsed.netloc:
        # Absolute URL - only allow it if it points back at this host.
        if parsed.netloc != request.url.netloc:
            return fallback
    path = parsed.path or "/"
    if not path.startswith("/") or path.startswith("//"):
        return fallback
    return path + (f"?{parsed.query}" if parsed.query else "")


# ── flash messages ──────────────────────────────────────────────


def set_flash(request: Request, message: str, level: str = "info") -> None:
    if "session" in request.scope:
        request.session["flash"] = {"message": message, "level": level}


def pop_flash(request: Request) -> Optional[dict]:
    if "session" not in request.scope:
        return None
    return request.session.pop("flash", None)


# ── current user ────────────────────────────────────────────────


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """The signed-in user bound to this request's session, or None."""
    if "session" not in request.scope:
        return None
    uid = request.session.get("uid")
    if not uid:
        return None
    user = db.get(User, int(uid))
    if user is None or not user.is_active:
        request.session.clear()
        return None
    request.state.user = user
    return user


def require_user(
    request: Request, user: Optional[User] = Depends(get_current_user)
) -> User:
    if user is None:
        raise AppRedirect("/login", "Please sign in to continue.", "info")
    return user


def require_verified(
    request: Request, user: User = Depends(require_user)
) -> User:
    """Signed in *and* confirmed their email address."""
    if not user.is_email_verified:
        raise AppRedirect(
            "/verify-email",
            "Please confirm your email address to use this feature.",
            "warning",
        )
    return user


def require_admin(request: Request, user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise AppRedirect("/", "That area is restricted.", "error")
    return user


def require_plan(feature: str):
    """
    Build a dependency gating a paid feature.

    Usage: `user: User = Depends(require_plan("ai"))`

    This returns a closure rather than a callable class on purpose. This module
    uses `from __future__ import annotations`, so every annotation is a string;
    FastAPI resolves those against the dependency's `__globals__`, which a class
    *instance* does not have. A callable class silently loses its annotations and
    FastAPI reinterprets `request: Request` as a required query parameter,
    answering every call with a 422. A nested function carries the module
    globals, so the annotations resolve.
    """

    def dependency(
        request: Request,
        user: User = Depends(require_verified),
        db: Session = Depends(get_db),
    ) -> User:
        if not user_has_feature(db, user, feature):
            raise AppRedirect(
                f"/pricing?feature={feature}",
                "That feature is part of a paid plan.",
                "info",
            )
        return user

    dependency.__name__ = f"require_plan_{feature}"
    return dependency


def subscription_context(db: Session, user: Optional[User]) -> dict:
    """Plan facts the nav and dashboards need on every page."""
    if user is None:
        return {"subscription": None, "is_paid": False, "can_ai": False, "can_catalog": False}
    sub = active_subscription(db, user.id)
    return {
        "subscription": sub,
        "is_paid": sub is not None,
        "can_ai": user_has_feature(db, user, "ai"),
        "can_catalog": user_has_feature(db, user, "catalog"),
    }
