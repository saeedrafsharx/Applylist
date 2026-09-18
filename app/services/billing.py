from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..i18n import _
from ..models import Payment, Plan, Subscription, User

log = logging.getLogger(__name__)


class BillingError(RuntimeError):
    """Raised when a payment cannot be started or verified."""


# ── Zarinpal error codes worth naming ───────────────────────────
ZARINPAL_ERRORS = {
    -9: "Validation error - check the amount and callback URL.",
    -10: "Merchant IP or ID is not valid.",
    -11: "Merchant is not active. Contact Zarinpal support.",
    -12: "Too many attempts. Try again later.",
    -15: "Terminal is suspended.",
    -16: "Merchant level does not allow this operation.",
    -30: "Terminal is not allowed to issue this transaction.",
    -31: "Terminal has no bank account attached.",
    -50: "The verified amount does not match the paid amount.",
    -51: "Payment was not completed.",
    -52: "Unexpected error. Contact Zarinpal support.",
    -53: "This payment belongs to another merchant.",
    -54: "Invalid authority.",
    101: "This payment was already verified.",
}


@dataclass(slots=True)
class PaymentStart:
    authority: str
    redirect_url: str


# ── plan / subscription helpers ─────────────────────────────────


def get_plan_by_code(db: Session, code: str) -> Optional[Plan]:
    return db.execute(select(Plan).where(Plan.code == code)).scalar_one_or_none()


def active_plans(db: Session) -> list[Plan]:
    return list(
        db.execute(
            select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order, Plan.id)
        ).scalars()
    )


def active_subscription(db: Session, user_id: int) -> Optional[Subscription]:
    """The user's live subscription, expiring any that have run out."""
    subs = list(
        db.execute(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == Subscription.STATUS_ACTIVE,
            )
            .order_by(Subscription.id.desc())
        ).scalars()
    )
    now = datetime.now(timezone.utc)
    for sub in subs:
        if sub.expires_at is None:
            return sub
        expires = sub.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires > now:
            return sub
        # Lazily mark it expired the first time we notice.
        sub.status = Subscription.STATUS_EXPIRED
    return None


def grant_subscription(
    db: Session,
    user: User,
    plan: Plan,
    *,
    source: str = Subscription.SOURCE_MANUAL,
    granted_by_user_id: Optional[int] = None,
    note: Optional[str] = None,
    days_override: Optional[int] = None,
) -> Subscription:
    """
    Activate `plan` for `user`.

    An existing subscription to the same plan is extended from whichever is
    later, now or its current expiry, so a renewal never costs the user days.
    Switching plans supersedes the old subscription instead of stacking.
    """
    now = datetime.now(timezone.utc)
    duration = days_override if days_override is not None else plan.duration_days
    current = active_subscription(db, user.id)

    if current and current.plan_id == plan.id and duration is not None:
        base = current.expires_at or now
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        base = max(base, now)
        current.expires_at = base + timedelta(days=duration)
        current.status = Subscription.STATUS_ACTIVE
        if note:
            current.note = note
        return current

    if current:
        current.status = Subscription.STATUS_CANCELED
        current.canceled_at = now

    sub = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        status=Subscription.STATUS_ACTIVE,
        source=source,
        started_at=now,
        expires_at=(now + timedelta(days=duration)) if duration is not None else None,
        granted_by_user_id=granted_by_user_id,
        note=note,
    )
    db.add(sub)
    db.flush()
    return sub


def revoke_subscription(db: Session, user: User, *, note: Optional[str] = None) -> bool:
    sub = active_subscription(db, user.id)
    if not sub:
        return False
    sub.status = Subscription.STATUS_CANCELED
    sub.canceled_at = datetime.now(timezone.utc)
    if note:
        sub.note = note
    return True


def user_has_feature(db: Session, user: Optional[User], feature: str) -> bool:
    """`feature` is 'catalog' or 'ai'. Admins always pass."""
    if user is None:
        return False
    if user.is_admin:
        return True
    sub = active_subscription(db, user.id)
    if not sub or not sub.plan:
        return False
    return bool(getattr(sub.plan, f"grants_{feature}", False))


# ── Zarinpal ────────────────────────────────────────────────────


def _zarinpal_error(code: int, fallback: str = "Payment gateway error") -> str:
    return ZARINPAL_ERRORS.get(code, f"{fallback} (code {code}).")


def start_payment(
    db: Session,
    user: User,
    plan: Plan,
    *,
    callback_url: str,
    description: Optional[str] = None,
) -> PaymentStart:
    """
    Create a pending Payment and ask Zarinpal for an authority.

    Returns the gateway URL to redirect the user to. Raises BillingError with a
    message safe to show the user.
    """
    if not settings.payments_enabled:
        raise BillingError(
            "Online payment isn't configured yet. Please contact support to activate a plan."
        )
    if plan.is_free:
        raise BillingError("That plan is free - no payment needed.")

    desc = description or f"ApplyList {plan.name} subscription"
    payload = {
        "merchant_id": settings.zarinpal_merchant_id,
        "amount": plan.price_rial,
        "callback_url": callback_url,
        "description": desc[:255],
        "metadata": {"email": user.email},
    }

    payment = Payment(
        user_id=user.id,
        plan_id=plan.id,
        provider="zarinpal",
        amount=plan.price_rial,
        currency=settings.zarinpal_currency,
        status=Payment.STATUS_PENDING,
        description=desc[:500],
        raw_request={k: v for k, v in payload.items() if k != "merchant_id"},
    )
    db.add(payment)
    db.flush()

    try:
        response = httpx.post(
            f"{settings.zarinpal_api_base}/request.json",
            json=payload,
            timeout=20.0,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        payment.status = Payment.STATUS_FAILED
        payment.error_message = f"Gateway unreachable: {exc}"
        log.exception("Zarinpal request failed for payment %s", payment.id)
        raise BillingError("Could not reach the payment gateway. Please try again.") from exc

    payment.raw_response = body
    data = body.get("data") or {}
    errors = body.get("errors") or {}
    code = data.get("code")

    if code != 100:
        err_code = errors.get("code") if isinstance(errors, dict) else None
        message = _zarinpal_error(err_code or code or 0, "Could not start payment")
        payment.status = Payment.STATUS_FAILED
        payment.error_message = message
        raise BillingError(message)

    authority = data.get("authority")
    if not authority:
        payment.status = Payment.STATUS_FAILED
        payment.error_message = "Gateway returned no authority."
        raise BillingError("The payment gateway returned an unexpected response.")

    payment.authority = authority
    return PaymentStart(
        authority=authority,
        redirect_url=f"{settings.zarinpal_startpay_base}/{authority}",
    )


def verify_payment(db: Session, authority: str, status: str) -> tuple[Payment, bool, str]:
    """
    Settle the callback. Returns (payment, succeeded, message).

    Verification is idempotent: Zarinpal code 101 means "already verified", and a
    payment already marked paid short-circuits, so a refreshed callback URL can
    never grant a second subscription.
    """
    payment = db.execute(
        select(Payment).where(Payment.authority == authority)
    ).scalar_one_or_none()
    if payment is None:
        raise BillingError("Unknown payment reference.")

    if payment.status == Payment.STATUS_PAID:
        return payment, True, "This payment was already confirmed."

    if status != "OK":
        payment.status = Payment.STATUS_CANCELED
        payment.error_message = "Payment was canceled at the gateway."
        return payment, False, "Payment was canceled."

    payload = {
        "merchant_id": settings.zarinpal_merchant_id,
        "amount": payment.amount,
        "authority": authority,
    }
    try:
        response = httpx.post(
            f"{settings.zarinpal_api_base}/verify.json",
            json=payload,
            timeout=20.0,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        payment.error_message = f"Verification unreachable: {exc}"
        log.exception("Zarinpal verify failed for payment %s", payment.id)
        raise BillingError(
            _("We couldn't confirm your payment with the gateway. If your account was charged, contact support with this reference: {ref}", ref=authority)
        ) from exc

    payment.raw_response = body
    data = body.get("data") or {}
    errors = body.get("errors") or {}
    code = data.get("code")

    if code in (100, 101):
        payment.status = Payment.STATUS_PAID
        payment.paid_at = datetime.now(timezone.utc)
        payment.ref_id = str(data.get("ref_id") or "")
        payment.card_pan = data.get("card_pan")
        return payment, True, "Payment confirmed."

    err_code = errors.get("code") if isinstance(errors, dict) else None
    message = _zarinpal_error(err_code or code or 0, "Payment verification failed")
    payment.status = Payment.STATUS_FAILED
    payment.error_message = message
    return payment, False, message
