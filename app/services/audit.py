from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ActivityLog


class Action:
    """Canonical action names. Keep these stable - the admin filter uses them."""

    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"
    REGISTER = "auth.register"
    EMAIL_VERIFIED = "auth.email_verified"
    VERIFY_RESENT = "auth.verification_resent"
    PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"
    PASSWORD_RESET = "auth.password_reset"

    CONTACT_CREATED = "contact.created"
    CONTACT_UPDATED = "contact.updated"
    CONTACT_DELETED = "contact.deleted"
    CONTACT_IMPORTED = "contact.imported"
    CONTACT_EXPORTED = "contact.exported"

    POSITION_CREATED = "position.created"
    POSITION_UPDATED = "position.updated"
    POSITION_DELETED = "position.deleted"

    CATALOG_VIEWED = "catalog.viewed"
    CATALOG_SAVED = "catalog.saved_to_contacts"
    CATALOG_BLOCKED = "catalog.blocked_unpaid"

    AI_MESSAGE = "ai.message"
    AI_BLOCKED = "ai.blocked_unpaid"
    AI_QUOTA_EXCEEDED = "ai.quota_exceeded"

    PAYMENT_STARTED = "billing.payment_started"
    PAYMENT_SUCCEEDED = "billing.payment_succeeded"
    PAYMENT_FAILED = "billing.payment_failed"
    SUBSCRIPTION_GRANTED = "billing.subscription_granted"
    SUBSCRIPTION_REVOKED = "billing.subscription_revoked"

    ADMIN_USER_UPDATED = "admin.user_updated"
    ADMIN_USER_DELETED = "admin.user_deleted"
    ADMIN_CATALOG_EDITED = "admin.catalog_edited"
    ADMIN_SCRAPE_TRIGGERED = "admin.scrape_triggered"
    ADMIN_TAKEDOWN_ACTIONED = "admin.takedown_actioned"


def client_ip(request: Request) -> Optional[str]:
    """Real client IP behind a reverse proxy, falling back to the socket peer."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()[:45]
    return request.client.host if request.client else None


def log_activity(
    db: Session,
    action: str,
    *,
    user_id: Optional[int] = None,
    request: Optional[Request] = None,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    summary: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> ActivityLog:
    entry = ActivityLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=(summary or "")[:500] or None,
        detail=detail,
        created_at=datetime.now(timezone.utc),
    )
    if request is not None:
        entry.ip = client_ip(request)
        entry.user_agent = request.headers.get("user-agent")
        entry.path = str(request.url.path)[:300]
    db.add(entry)
    return entry


def recent_activity(db: Session, limit: int = 50) -> list[ActivityLog]:
    return list(
        db.execute(
            select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(limit)
        ).scalars()
    )


def activity_counts_by_day(db: Session, days: int = 30) -> list[tuple[str, int]]:
    """(YYYY-MM-DD, count) for the last `days` days, oldest first, no gaps."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    day = func.date_trunc("day", ActivityLog.created_at)
    rows = db.execute(
        select(day.label("d"), func.count(ActivityLog.id))
        .where(ActivityLog.created_at >= since)
        .group_by(day)
        .order_by(day)
    ).all()
    found = {r[0].date().isoformat(): r[1] for r in rows if r[0] is not None}

    today = datetime.now(timezone.utc).date()
    out: list[tuple[str, int]] = []
    for offset in range(days - 1, -1, -1):
        key = (today - timedelta(days=offset)).isoformat()
        out.append((key, found.get(key, 0)))
    return out
