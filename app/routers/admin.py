from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from ..config import settings
from ..db import get_db
from ..deps import render, require_admin, safe_back, set_flash
from ..models import (
    ActivityLog,
    AIMessage,
    AIUsage,
    CatalogJobTitle,
    CatalogProfessor,
    CatalogUniversity,
    Contact,
    Conversation,
    Payment,
    Plan,
    Position,
    ScrapeRun,
    ScrapeSource,
    Subscription,
    TakedownRequest,
    User,
)
from ..services.audit import Action, activity_counts_by_day, log_activity
from ..services.billing import active_subscription, grant_subscription, revoke_subscription
from ..services.scraper.runner import expire_stale_runs, is_running, run_in_background

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

PAGE_SIZE = 40


def _page(value: int) -> int:
    return max(1, value)


def _count(db: Session, stmt) -> int:
    return int(db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one())


# ── dashboard ───────────────────────────────────────────────────


@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    def scalar(stmt) -> int:
        return int(db.execute(stmt).scalar_one() or 0)

    users_total = scalar(select(func.count(User.id)))
    users_verified = scalar(select(func.count(User.id)).where(User.is_email_verified.is_(True)))
    users_new_week = scalar(select(func.count(User.id)).where(User.created_at >= week_ago))
    users_active_day = scalar(
        select(func.count(func.distinct(ActivityLog.user_id))).where(
            ActivityLog.created_at >= day_ago, ActivityLog.user_id.is_not(None)
        )
    )
    users_active_month = scalar(
        select(func.count(func.distinct(ActivityLog.user_id))).where(
            ActivityLog.created_at >= month_ago, ActivityLog.user_id.is_not(None)
        )
    )

    subs_active = scalar(
        select(func.count(Subscription.id)).where(
            Subscription.status == Subscription.STATUS_ACTIVE,
            or_(Subscription.expires_at.is_(None), Subscription.expires_at > now),
        )
    )
    revenue_total = scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == Payment.STATUS_PAID
        )
    )
    revenue_month = scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == Payment.STATUS_PAID, Payment.paid_at >= month_ago
        )
    )

    stats = {
        "users_total": users_total,
        "users_verified": users_verified,
        "users_unverified": users_total - users_verified,
        "users_new_week": users_new_week,
        "users_active_day": users_active_day,
        "users_active_month": users_active_month,
        "subs_active": subs_active,
        "conversion": round(100 * subs_active / users_total, 1) if users_total else 0.0,
        "revenue_total_toman": revenue_total // 10,
        "revenue_month_toman": revenue_month // 10,
        "payments_pending": scalar(
            select(func.count(Payment.id)).where(Payment.status == Payment.STATUS_PENDING)
        ),
        "contacts": scalar(select(func.count(Contact.id))),
        "positions": scalar(select(func.count(Position.id))),
        "catalog_professors": scalar(
            select(func.count(CatalogProfessor.id)).where(
                CatalogProfessor.is_published.is_(True), CatalogProfessor.is_removed.is_(False)
            )
        ),
        "catalog_universities": scalar(select(func.count(CatalogUniversity.id))),
        "catalog_jobs": scalar(select(func.count(CatalogJobTitle.id))),
        "takedowns_open": scalar(
            select(func.count(TakedownRequest.id)).where(
                TakedownRequest.status == TakedownRequest.STATUS_OPEN
            )
        ),
        "ai_messages_month": scalar(
            select(func.count(AIMessage.id)).where(
                AIMessage.created_at >= month_ago, AIMessage.role == AIMessage.ROLE_ASSISTANT
            )
        ),
        "ai_tokens_month": scalar(
            select(
                func.coalesce(func.sum(AIMessage.input_tokens + AIMessage.output_tokens), 0)
            ).where(AIMessage.created_at >= month_ago)
        ),
    }

    signups = db.execute(
        select(func.date_trunc("day", User.created_at).label("d"), func.count(User.id))
        .where(User.created_at >= month_ago)
        .group_by("d")
        .order_by("d")
    ).all()
    signup_series = [(r[0].date().isoformat(), r[1]) for r in signups if r[0]]

    plan_counts = db.execute(
        select(Plan.name, func.count(Subscription.id))
        .join(Subscription, Subscription.plan_id == Plan.id)
        .where(
            Subscription.status == Subscription.STATUS_ACTIVE,
            or_(Subscription.expires_at.is_(None), Subscription.expires_at > now),
        )
        .group_by(Plan.name)
        .order_by(func.count(Subscription.id).desc())
    ).all()

    return render(
        request,
        "admin/dashboard.html",
        {
            "stats": stats,
            "activity_series": activity_counts_by_day(db, 30),
            "signup_series": signup_series,
            "plan_counts": plan_counts,
            "recent": list(
                db.execute(
                    select(ActivityLog)
                    .options(joinedload(ActivityLog.user))
                    .order_by(ActivityLog.created_at.desc())
                    .limit(15)
                ).scalars()
            ),
            "health": {
                "email": settings.email_enabled,
                "payments": settings.payments_enabled,
                "ai": settings.ai_enabled,
            },
        },
    )


# ── users ───────────────────────────────────────────────────────


@router.get("/users")
def users_list(
    request: Request,
    q: str = "",
    plan: str = "",
    state: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    page = _page(page)
    stmt = select(User)

    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(User.username.ilike(like), User.email.ilike(like), User.full_name.ilike(like))
        )
    if state == "unverified":
        stmt = stmt.where(User.is_email_verified.is_(False))
    elif state == "suspended":
        stmt = stmt.where(User.is_active.is_(False))
    elif state == "admin":
        stmt = stmt.where(User.is_admin.is_(True))

    if plan:
        now = datetime.now(timezone.utc)
        sub_q = (
            select(Subscription.user_id)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                Plan.code == plan,
                Subscription.status == Subscription.STATUS_ACTIVE,
                or_(Subscription.expires_at.is_(None), Subscription.expires_at > now),
            )
        )
        if plan == "free":
            stmt = stmt.where(User.id.not_in(
                select(Subscription.user_id).where(
                    Subscription.status == Subscription.STATUS_ACTIVE,
                    or_(Subscription.expires_at.is_(None), Subscription.expires_at > now),
                )
            ))
        else:
            stmt = stmt.where(User.id.in_(sub_q))

    total = _count(db, stmt)
    rows = list(
        db.execute(
            stmt.order_by(User.created_at.desc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        ).scalars()
    )

    subs = {
        s.user_id: s
        for s in db.execute(
            select(Subscription)
            .options(joinedload(Subscription.plan))
            .where(
                Subscription.user_id.in_([u.id for u in rows] or [0]),
                Subscription.status == Subscription.STATUS_ACTIVE,
            )
            .order_by(Subscription.id.desc())
        ).scalars()
    }

    return render(
        request,
        "admin/users.html",
        {
            "rows": rows,
            "subs": subs,
            "total": total,
            "page": page,
            "pages": max(1, math.ceil(total / PAGE_SIZE)),
            "q": q,
            "plan": plan,
            "state": state,
            "plans": list(db.execute(select(Plan).order_by(Plan.sort_order)).scalars()),
        },
    )


@router.get("/users/{user_id}")
def user_detail(request: Request, user_id: int, db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        set_flash(request, "No such user.", "error")
        return RedirectResponse("/admin/users", status_code=303)

    activity = list(
        db.execute(
            select(ActivityLog)
            .where(ActivityLog.user_id == user_id)
            .order_by(ActivityLog.created_at.desc())
            .limit(100)
        ).scalars()
    )
    payments = list(
        db.execute(
            select(Payment)
            .options(joinedload(Payment.plan))
            .where(Payment.user_id == user_id)
            .order_by(Payment.id.desc())
        ).scalars()
    )
    subscriptions = list(
        db.execute(
            select(Subscription)
            .options(joinedload(Subscription.plan))
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.id.desc())
        ).scalars()
    )
    usage = list(
        db.execute(
            select(AIUsage)
            .where(AIUsage.user_id == user_id)
            .order_by(AIUsage.period.desc())
            .limit(12)
        ).scalars()
    )

    counts = {
        "contacts": int(
            db.execute(
                select(func.count(Contact.id)).where(Contact.owner_id == user_id)
            ).scalar_one()
        ),
        "positions": int(
            db.execute(
                select(func.count(Position.id)).where(Position.owner_id == user_id)
            ).scalar_one()
        ),
        "conversations": int(
            db.execute(
                select(func.count(Conversation.id)).where(Conversation.user_id == user_id)
            ).scalar_one()
        ),
    }

    return render(
        request,
        "admin/user_detail.html",
        {
            "u": target,
            "activity": activity,
            "payments": payments,
            "subscriptions": subscriptions,
            "usage": usage,
            "counts": counts,
            "current_sub": active_subscription(db, user_id),
            "plans": list(
                db.execute(
                    select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order)
                ).scalars()
            ),
        },
    )


@router.post("/users/{user_id}/flags")
def update_user_flags(
    request: Request,
    user_id: int,
    is_active: Optional[str] = Form(None),
    is_admin: Optional[str] = Form(None),
    is_email_verified: Optional[str] = Form(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        return RedirectResponse("/admin/users", status_code=303)

    was = {
        "is_active": target.is_active,
        "is_admin": target.is_admin,
        "is_email_verified": target.is_email_verified,
    }

    target.is_active = is_active is not None
    target.is_email_verified = is_email_verified is not None
    if target.is_email_verified and not was["is_email_verified"]:
        target.email_verified_at = datetime.now(timezone.utc)

    wants_admin = is_admin is not None
    if target.id == admin.id and not wants_admin:
        set_flash(request, "You can't remove your own admin access.", "warning")
    else:
        target.is_admin = wants_admin

    if target.id == admin.id and not target.is_active:
        target.is_active = True
        set_flash(request, "You can't suspend your own account.", "warning")

    log_activity(
        db, Action.ADMIN_USER_UPDATED, user_id=admin.id, request=request,
        target_type="user", target_id=target.id,
        summary=f"Updated flags for {target.username}",
        detail={"before": was, "after": {
            "is_active": target.is_active,
            "is_admin": target.is_admin,
            "is_email_verified": target.is_email_verified,
        }},
    )
    set_flash(request, f"Updated {target.username}.", "success")
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/subscription")
def set_subscription(
    request: Request,
    user_id: int,
    plan_code: str = Form(...),
    days: str = Form(""),
    note: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        return RedirectResponse("/admin/users", status_code=303)

    if plan_code == "__revoke__":
        if revoke_subscription(db, target, note=note or "Revoked by admin"):
            log_activity(
                db, Action.SUBSCRIPTION_REVOKED, user_id=admin.id, request=request,
                target_type="user", target_id=target.id,
                summary=f"Revoked {target.username}'s subscription",
            )
            set_flash(request, f"Revoked {target.username}'s subscription.", "info")
        else:
            set_flash(request, "That user has no active subscription.", "warning")
        return RedirectResponse(f"/admin/users/{user_id}", status_code=303)

    plan = db.execute(select(Plan).where(Plan.code == plan_code)).scalar_one_or_none()
    if plan is None:
        set_flash(request, "Unknown plan.", "error")
        return RedirectResponse(f"/admin/users/{user_id}", status_code=303)
    if plan.is_free:
        # "Granting" Free would supersede a paid plan - a silent downgrade.
        set_flash(request, "To move someone to the free plan, revoke their subscription.", "warning")
        return RedirectResponse(f"/admin/users/{user_id}", status_code=303)

    override: Optional[int] = None
    if days.strip():
        try:
            override = max(1, int(days.strip()))
        except ValueError:
            set_flash(request, "Days must be a whole number.", "error")
            return RedirectResponse(f"/admin/users/{user_id}", status_code=303)

    sub = grant_subscription(
        db, target, plan,
        source=Subscription.SOURCE_MANUAL,
        granted_by_user_id=admin.id,
        note=note or f"Granted by {admin.username}",
        days_override=override,
    )
    log_activity(
        db, Action.SUBSCRIPTION_GRANTED, user_id=admin.id, request=request,
        target_type="user", target_id=target.id,
        summary=f"Granted {plan.name} to {target.username}",
        detail={"plan": plan.code, "expires_at":
                sub.expires_at.isoformat() if sub.expires_at else None},
    )
    set_flash(request, f"{plan.name} activated for {target.username}.", "success")
    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/delete")
def delete_user(
    request: Request,
    user_id: int,
    confirm: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        return RedirectResponse("/admin/users", status_code=303)
    if target.id == admin.id:
        set_flash(request, "You can't delete your own account.", "error")
        return RedirectResponse(f"/admin/users/{user_id}", status_code=303)
    if confirm.strip() != target.username:
        set_flash(
            request,
            "Type the username exactly to confirm deletion.",
            "warning",
        )
        return RedirectResponse(f"/admin/users/{user_id}", status_code=303)

    username = target.username
    log_activity(
        db, Action.ADMIN_USER_DELETED, user_id=admin.id, request=request,
        target_type="user", target_id=target.id,
        summary=f"Deleted user {username}",
        detail={"email": target.email},
    )
    db.delete(target)
    set_flash(request, f"Deleted {username} and all their data.", "info")
    return RedirectResponse("/admin/users", status_code=303)


# ── activity ────────────────────────────────────────────────────


@router.get("/activity")
def activity_feed(
    request: Request,
    q: str = "",
    action: str = "",
    user_id: int = 0,
    page: int = 1,
    db: Session = Depends(get_db),
):
    page = _page(page)
    stmt = select(ActivityLog).options(joinedload(ActivityLog.user))

    if action:
        stmt = stmt.where(ActivityLog.action == action)
    if user_id:
        stmt = stmt.where(ActivityLog.user_id == user_id)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(ActivityLog.summary.ilike(like), ActivityLog.ip.ilike(like),
                ActivityLog.path.ilike(like))
        )

    total = _count(db, stmt)
    rows = list(
        db.execute(
            stmt.order_by(ActivityLog.created_at.desc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        ).scalars()
    )
    actions = [
        row for row in db.execute(
            select(ActivityLog.action).distinct().order_by(ActivityLog.action)
        ).scalars()
    ]

    return render(
        request,
        "admin/activity.html",
        {
            "rows": rows, "total": total, "page": page,
            "pages": max(1, math.ceil(total / PAGE_SIZE)),
            "q": q, "action": action, "user_id": user_id, "actions": actions,
        },
    )


# ── billing ─────────────────────────────────────────────────────


@router.get("/payments")
def payments_list(
    request: Request,
    status: str = "",
    q: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    page = _page(page)
    stmt = select(Payment).options(
        joinedload(Payment.user), joinedload(Payment.plan)
    )
    if status:
        stmt = stmt.where(Payment.status == status)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.join(User, User.id == Payment.user_id).where(
            or_(
                Payment.authority.ilike(like),
                Payment.ref_id.ilike(like),
                User.username.ilike(like),
                User.email.ilike(like),
            )
        )

    total = _count(db, stmt)
    rows = list(
        db.execute(
            stmt.order_by(Payment.id.desc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        ).scalars()
    )

    totals = db.execute(
        select(Payment.status, func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0))
        .group_by(Payment.status)
    ).all()

    return render(
        request,
        "admin/payments.html",
        {
            "rows": rows, "total": total, "page": page,
            "pages": max(1, math.ceil(total / PAGE_SIZE)),
            "status": status, "q": q, "totals": totals,
        },
    )


@router.get("/plans")
def plans_list(request: Request, db: Session = Depends(get_db)):
    plans = list(db.execute(select(Plan).order_by(Plan.sort_order, Plan.id)).scalars())
    now = datetime.now(timezone.utc)
    counts = dict(
        db.execute(
            select(Subscription.plan_id, func.count(Subscription.id))
            .where(
                Subscription.status == Subscription.STATUS_ACTIVE,
                or_(Subscription.expires_at.is_(None), Subscription.expires_at > now),
            )
            .group_by(Subscription.plan_id)
        ).all()
    )
    return render(request, "admin/plans.html", {"plans": plans, "counts": counts})


@router.post("/plans/{plan_id}")
def update_plan(
    request: Request,
    plan_id: int,
    name: str = Form(...),
    description: str = Form(""),
    price_toman: int = Form(0),
    duration_days: str = Form(""),
    ai_monthly_quota: str = Form(""),
    grants_catalog: Optional[str] = Form(None),
    grants_ai: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    sort_order: int = Form(0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plan = db.get(Plan, plan_id)
    if plan is None:
        return RedirectResponse("/admin/plans", status_code=303)

    plan.name = name.strip()
    plan.description = description.strip() or None
    plan.price_rial = max(0, int(price_toman)) * 10
    plan.duration_days = int(duration_days) if duration_days.strip().isdigit() else None
    plan.ai_monthly_quota = (
        int(ai_monthly_quota) if ai_monthly_quota.strip().isdigit() else None
    )
    plan.grants_catalog = grants_catalog is not None
    plan.grants_ai = grants_ai is not None
    plan.is_active = is_active is not None
    plan.sort_order = int(sort_order)

    log_activity(
        db, Action.ADMIN_CATALOG_EDITED, user_id=admin.id, request=request,
        target_type="plan", target_id=plan.id, summary=f"Updated plan {plan.code}",
    )
    set_flash(request, f"Saved {plan.name}.", "success")
    return RedirectResponse("/admin/plans", status_code=303)


# ── catalog ─────────────────────────────────────────────────────


@router.get("/catalog")
def catalog_list(
    request: Request,
    q: str = "",
    university: str = "",
    state: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    page = _page(page)
    stmt = select(CatalogProfessor).options(joinedload(CatalogProfessor.university))

    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                CatalogProfessor.name.ilike(like),
                CatalogProfessor.email.ilike(like),
                CatalogProfessor.research_focus.ilike(like),
            )
        )
    if university:
        stmt = stmt.join(CatalogUniversity).where(CatalogUniversity.slug == university)
    if state == "unpublished":
        stmt = stmt.where(CatalogProfessor.is_published.is_(False))
    elif state == "removed":
        stmt = stmt.where(CatalogProfessor.is_removed.is_(True))
    elif state == "no_email":
        stmt = stmt.where(CatalogProfessor.email.is_(None))

    total = _count(db, stmt)
    rows = list(
        db.execute(
            stmt.order_by(CatalogProfessor.updated_at.desc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        ).scalars()
    )

    return render(
        request,
        "admin/catalog.html",
        {
            "rows": rows, "total": total, "page": page,
            "pages": max(1, math.ceil(total / PAGE_SIZE)),
            "q": q, "university": university, "state": state,
            "universities": list(
                db.execute(select(CatalogUniversity).order_by(CatalogUniversity.name)).scalars()
            ),
            "open_takedowns": int(
                db.execute(
                    select(func.count(TakedownRequest.id)).where(
                        TakedownRequest.status == TakedownRequest.STATUS_OPEN
                    )
                ).scalar_one()
            ),
        },
    )


@router.post("/catalog/{professor_id}")
def update_catalog_entry(
    request: Request,
    professor_id: int,
    name: str = Form(...),
    title: str = Form(""),
    department: str = Form(""),
    email: str = Form(""),
    research_focus: str = Form(""),
    profile_url: str = Form(""),
    is_published: Optional[str] = Form(None),
    verified: Optional[str] = Form(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    entry = db.get(CatalogProfessor, professor_id)
    if entry is None:
        return RedirectResponse("/admin/catalog", status_code=303)

    entry.name = name.strip()
    entry.title = title.strip() or None
    entry.department = department.strip() or None
    entry.email = email.strip().lower() or None
    entry.research_focus = research_focus.strip() or None
    entry.profile_url = profile_url.strip() or None
    entry.is_published = is_published is not None
    if verified is not None and entry.verified_at is None:
        entry.verified_at = datetime.now(timezone.utc)
    elif verified is None:
        entry.verified_at = None

    log_activity(
        db, Action.ADMIN_CATALOG_EDITED, user_id=admin.id, request=request,
        target_type="catalog_professor", target_id=entry.id,
        summary=f"Edited catalog entry {entry.name}",
    )
    set_flash(request, f"Saved {entry.name}.", "success")
    return RedirectResponse(safe_back(request, "/admin/catalog"), status_code=303)


@router.post("/catalog/{professor_id}/remove")
def remove_catalog_entry(
    request: Request,
    professor_id: int,
    reason: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Takedown. The row is kept but hidden, so a later scrape can't undo it."""
    entry = db.get(CatalogProfessor, professor_id)
    if entry is None:
        return RedirectResponse("/admin/catalog", status_code=303)

    entry.is_removed = True
    entry.is_published = False
    entry.removed_reason = reason.strip() or "Removed by admin"
    entry.removed_at = datetime.now(timezone.utc)

    log_activity(
        db, Action.ADMIN_TAKEDOWN_ACTIONED, user_id=admin.id, request=request,
        target_type="catalog_professor", target_id=entry.id,
        summary=f"Removed {entry.name} from the catalog",
        detail={"reason": entry.removed_reason},
    )
    set_flash(request, f"{entry.name} removed from the catalog.", "info")
    return RedirectResponse(safe_back(request, "/admin/catalog"), status_code=303)


@router.post("/catalog/{professor_id}/restore")
def restore_catalog_entry(
    request: Request,
    professor_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    entry = db.get(CatalogProfessor, professor_id)
    if entry is not None:
        entry.is_removed = False
        entry.is_published = True
        entry.removed_reason = None
        entry.removed_at = None
        log_activity(
            db, Action.ADMIN_CATALOG_EDITED, user_id=admin.id, request=request,
            target_type="catalog_professor", target_id=entry.id,
            summary=f"Restored {entry.name}",
        )
        set_flash(request, f"{entry.name} restored.", "success")
    return RedirectResponse(safe_back(request, "/admin/catalog"), status_code=303)


@router.get("/takedowns")
def takedowns(request: Request, status: str = "open", db: Session = Depends(get_db)):
    stmt = select(TakedownRequest).options(joinedload(TakedownRequest.professor))
    if status:
        stmt = stmt.where(TakedownRequest.status == status)
    rows = list(db.execute(stmt.order_by(TakedownRequest.created_at.desc()).limit(200)).scalars())
    return render(request, "admin/takedowns.html", {"rows": rows, "status": status})


@router.post("/takedowns/{request_id}")
def handle_takedown(
    request: Request,
    request_id: int,
    decision: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(TakedownRequest, request_id)
    if item is None:
        return RedirectResponse("/admin/takedowns", status_code=303)

    if decision == "approve":
        item.status = TakedownRequest.STATUS_ACTIONED
        if item.professor is not None:
            item.professor.is_removed = True
            item.professor.is_published = False
            item.professor.removed_reason = "Takedown request approved"
            item.professor.removed_at = datetime.now(timezone.utc)
        set_flash(request, "Takedown approved; the entry is now hidden.", "success")
    else:
        item.status = TakedownRequest.STATUS_REJECTED
        if item.professor is not None:
            item.professor.is_published = True
        set_flash(request, "Takedown rejected; the entry is visible again.", "info")

    item.handled_at = datetime.now(timezone.utc)
    item.handled_by_user_id = admin.id
    log_activity(
        db, Action.ADMIN_TAKEDOWN_ACTIONED, user_id=admin.id, request=request,
        target_type="takedown_request", target_id=item.id,
        summary=f"Takedown {decision} for {item.subject_name}",
    )
    return RedirectResponse("/admin/takedowns", status_code=303)


@router.get("/jobs")
def job_titles(request: Request, q: str = "", db: Session = Depends(get_db)):
    stmt = select(CatalogJobTitle)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(CatalogJobTitle.title.ilike(like), CatalogJobTitle.description.ilike(like))
        )
    rows = list(
        db.execute(stmt.order_by(CatalogJobTitle.field, CatalogJobTitle.title).limit(500)).scalars()
    )
    return render(request, "admin/jobs.html", {"rows": rows, "q": q})


@router.post("/jobs")
def create_job_title(
    request: Request,
    title: str = Form(...),
    field: str = Form(""),
    level: str = Form(""),
    description: str = Form(""),
    typical_requirements: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    existing = db.execute(
        select(CatalogJobTitle).where(
            CatalogJobTitle.title == title.strip(),
            CatalogJobTitle.field == (field.strip() or None),
        )
    ).scalar_one_or_none()
    if existing is not None:
        set_flash(request, "That title already exists in this field.", "warning")
        return RedirectResponse("/admin/jobs", status_code=303)

    db.add(
        CatalogJobTitle(
            title=title.strip(),
            field=field.strip() or None,
            level=level.strip() or None,
            description=description.strip() or None,
            typical_requirements=typical_requirements.strip() or None,
            is_published=True,
        )
    )
    set_flash(request, f"Added {title.strip()}.", "success")
    return RedirectResponse("/admin/jobs", status_code=303)


@router.post("/jobs/{job_id}/delete")
def delete_job_title(
    request: Request, job_id: int, db: Session = Depends(get_db)
):
    item = db.get(CatalogJobTitle, job_id)
    if item is not None:
        db.delete(item)
        set_flash(request, "Deleted.", "info")
    return RedirectResponse("/admin/jobs", status_code=303)


# ── scraper ─────────────────────────────────────────────────────


@router.get("/scraper")
def scraper_home(request: Request, db: Session = Depends(get_db)):
    expire_stale_runs(db)
    sources = list(db.execute(select(ScrapeSource).order_by(ScrapeSource.label)).scalars())
    runs = list(
        db.execute(
            select(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(50)
        ).scalars()
    )
    from ..services.scraper.parsers import PARSER_HELP, PARSERS

    running_keys = {r.source_key for r in runs if r.status == ScrapeRun.STATUS_RUNNING}
    return render(
        request,
        "admin/scraper.html",
        {
            "sources": sources,
            "runs": runs,
            "running_keys": running_keys,
            "parsers": list(PARSERS),
            "parser_help": PARSER_HELP,
            "user_agent": settings.scraper_user_agent,
            "respect_robots": settings.scraper_respect_robots,
        },
    )


@router.post("/scraper/sources")
def create_source(
    request: Request,
    key: str = Form(...),
    label: str = Form(...),
    start_url: str = Form(...),
    parser: str = Form("auto"),
    university_slug: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    from ..services.scraper.parsers import PARSERS

    existing = db.execute(
        select(ScrapeSource).where(ScrapeSource.key == key.strip())
    ).scalar_one_or_none()
    if existing is not None:
        set_flash(request, f"A source with key {key!r} already exists.", "warning")
        return RedirectResponse("/admin/scraper", status_code=303)
    if parser not in PARSERS:
        set_flash(request, f"Unknown parser {parser!r}.", "error")
        return RedirectResponse("/admin/scraper", status_code=303)

    db.add(
        ScrapeSource(
            key=key.strip(),
            label=label.strip(),
            start_url=start_url.strip(),
            parser=parser,
            university_slug=university_slug.strip(),
            notes=notes.strip() or None,
            enabled=True,
        )
    )
    set_flash(request, f"Added source {label.strip()}. Try a dry run first to check what it finds.", "success")
    return RedirectResponse("/admin/scraper", status_code=303)


@router.post("/scraper/sources/{source_id}/toggle")
def toggle_source(request: Request, source_id: int, db: Session = Depends(get_db)):
    source = db.get(ScrapeSource, source_id)
    if source is not None:
        source.enabled = not source.enabled
    return RedirectResponse("/admin/scraper", status_code=303)


@router.post("/scraper/sources/{source_id}/parser")
def change_parser(
    request: Request, source_id: int, parser: str = Form(...), db: Session = Depends(get_db)
):
    from ..services.scraper.parsers import PARSERS

    source = db.get(ScrapeSource, source_id)
    if source is not None and parser in PARSERS:
        source.parser = parser
        set_flash(request, f"{source.label} now uses the {parser} parser.", "success")
    return RedirectResponse("/admin/scraper", status_code=303)


@router.post("/scraper/sources/{source_id}/delete")
def delete_source(request: Request, source_id: int, db: Session = Depends(get_db)):
    source = db.get(ScrapeSource, source_id)
    if source is not None:
        db.delete(source)
        set_flash(request, "Source deleted. Records already collected are kept.", "info")
    return RedirectResponse("/admin/scraper", status_code=303)


@router.post("/scraper/run/{source_id}")
def trigger_run(
    request: Request,
    background: BackgroundTasks,
    source_id: int,
    dry_run: Optional[str] = Form(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Start a crawl of one source in the background.

    A polite crawl takes minutes (sites commonly set Crawl-delay: 10), far longer
    than a reverse proxy will hold a request open, so it runs after the response
    is sent. The scraper page refreshes itself until the run finishes.
    """
    source = db.get(ScrapeSource, source_id)
    if source is None:
        return RedirectResponse("/admin/scraper", status_code=303)
    if is_running(db, source.key):
        set_flash(request, f"{source.label} is already running.", "warning")
        return RedirectResponse("/admin/scraper", status_code=303)

    is_dry = dry_run is not None
    background.add_task(run_in_background, [source.id], admin.id, is_dry)
    log_activity(
        db, Action.ADMIN_SCRAPE_TRIGGERED, user_id=admin.id, request=request,
        target_type="scrape_source", target_id=source.id,
        summary=f"Started {'dry run of ' if is_dry else ''}{source.key}",
    )
    set_flash(
        request,
        f"Started {'a dry run of ' if is_dry else ''}{source.label}. "
        "This page refreshes until it finishes.",
        "info",
    )
    return RedirectResponse("/admin/scraper", status_code=303)


@router.post("/scraper/run-all")
def trigger_all(
    request: Request,
    background: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Crawl every enabled source, one after another, in the background."""
    sources = [
        s for s in db.execute(
            select(ScrapeSource).where(ScrapeSource.enabled.is_(True)).order_by(ScrapeSource.label)
        ).scalars()
        if not is_running(db, s.key)
    ]
    if not sources:
        set_flash(request, "No enabled sources to run.", "warning")
        return RedirectResponse("/admin/scraper", status_code=303)

    background.add_task(run_in_background, [s.id for s in sources], admin.id, False)
    log_activity(
        db, Action.ADMIN_SCRAPE_TRIGGERED, user_id=admin.id, request=request,
        target_type="scrape_source", summary=f"Started all enabled sources ({len(sources)})",
    )
    set_flash(request, f"Started {len(sources)} source(s). They run one after another.", "info")
    return RedirectResponse("/admin/scraper", status_code=303)


@router.get("/scraper/runs/{run_id}")
def run_detail(request: Request, run_id: int, db: Session = Depends(get_db)):
    run = db.get(ScrapeRun, run_id)
    if run is None:
        return RedirectResponse("/admin/scraper", status_code=303)
    return render(request, "admin/scrape_run.html", {"run": run})
