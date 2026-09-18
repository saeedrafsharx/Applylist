from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import get_current_user, render, require_verified, set_flash, subscription_context
from ..models import Payment, Plan, Subscription, User
from ..services import email as email_service
from ..services.audit import Action, log_activity
from ..services.billing import (
    BillingError,
    active_plans,
    active_subscription,
    grant_subscription,
    start_payment,
    verify_payment,
)

router = APIRouter(tags=["billing"])


@router.get("/pricing")
def pricing(
    request: Request,
    feature: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plans = active_plans(db)
    reason = {
        "ai": "The AI assistant is part of a paid plan.",
        "catalog": "The contact database is part of a paid plan.",
    }.get(feature)

    return render(
        request,
        "billing/pricing.html",
        {
            "plans": plans,
            "reason": reason,
            "payments_enabled": settings.payments_enabled,
            **subscription_context(db, user),
        },
    )


@router.get("/billing")
def billing_home(
    request: Request,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    payments = list(
        db.execute(
            select(Payment)
            .where(Payment.user_id == user.id)
            .order_by(Payment.id.desc())
            .limit(50)
        ).scalars()
    )
    return render(
        request,
        "billing/account.html",
        {"payments": payments, "plans": active_plans(db), **subscription_context(db, user)},
    )


@router.post("/billing/subscribe/{plan_code}")
def subscribe(
    request: Request,
    plan_code: str,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    plan = db.execute(select(Plan).where(Plan.code == plan_code)).scalar_one_or_none()
    if plan is None or not plan.is_active:
        set_flash(request, "That plan isn't available.", "error")
        return RedirectResponse("/pricing", status_code=303)

    if plan.is_free:
        set_flash(request, "You're already on the free plan.", "info")
        return RedirectResponse("/pricing", status_code=303)

    callback = f"{settings.base_url.rstrip('/')}/billing/callback"
    try:
        start = start_payment(db, user, plan, callback_url=callback)
    except BillingError as exc:
        log_activity(
            db, Action.PAYMENT_FAILED, user_id=user.id, request=request,
            summary=f"Could not start payment for {plan.code}: {exc}",
        )
        set_flash(request, str(exc), "error")
        return RedirectResponse("/pricing", status_code=303)

    log_activity(
        db, Action.PAYMENT_STARTED, user_id=user.id, request=request,
        summary=f"Started payment for {plan.name}",
        detail={"plan": plan.code, "amount_rial": plan.price_rial,
                "authority": start.authority},
    )
    return RedirectResponse(start.redirect_url, status_code=303)


@router.get("/billing/callback")
def billing_callback(
    request: Request,
    Authority: str = "",
    Status: str = "",
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Zarinpal returns the buyer here with ?Authority=&Status=.

    Verification is idempotent, so a refreshed or replayed callback settles to
    the same result instead of granting a second subscription.
    """
    if not Authority:
        set_flash(request, "That payment link is incomplete.", "error")
        return RedirectResponse("/pricing", status_code=303)

    try:
        payment, ok, message = verify_payment(db, Authority, Status)
    except BillingError as exc:
        set_flash(request, str(exc), "error")
        return RedirectResponse("/billing", status_code=303)

    payer = db.get(User, payment.user_id)
    if not ok or payer is None:
        log_activity(
            db, Action.PAYMENT_FAILED, user_id=payment.user_id, request=request,
            target_type="payment", target_id=payment.id, summary=message,
        )
        return render(
            request,
            "billing/result.html",
            {"ok": False, "message": message, "payment": payment,
             **subscription_context(db, user)},
        )

    if payment.subscription_id is not None:
        # Already settled on an earlier callback - don't grant twice.
        subscription = db.get(Subscription, payment.subscription_id)
    else:
        subscription = grant_subscription(
            db,
            payer,
            payment.plan,
            source="zarinpal",
            note=f"Zarinpal ref {payment.ref_id}",
        )
        payment.subscription_id = subscription.id

        log_activity(
            db, Action.PAYMENT_SUCCEEDED, user_id=payer.id, request=request,
            target_type="payment", target_id=payment.id,
            summary=f"Paid {payment.amount_toman:,} Toman for {payment.plan.name}",
            detail={"ref_id": payment.ref_id, "plan": payment.plan.code},
        )
        email_service.send_subscription_receipt(
            payer.email,
            payer.username,
            payment.plan.name,
            payment.ref_id or "—",
            payment.amount_toman,
            subscription.expires_at.strftime("%Y-%m-%d") if subscription.expires_at else None,
        )

    set_flash(request, f"{payment.plan.name} is now active. Thank you!", "success")
    return render(
        request,
        "billing/result.html",
        {
            "ok": True,
            "message": message,
            "payment": payment,
            "subscription": active_subscription(db, payer.id),
            **subscription_context(db, payer),
        },
    )
