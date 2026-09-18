from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from pydantic import EmailStr, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import get_current_user, render, require_user, set_flash
from ..models import EmailToken, User
from ..schemas import LoginForm, RegisterForm
from ..services import email as email_service
from ..services.audit import Action, client_ip, log_activity
from ..services.security import (
    generate_token,
    get_password_hash,
    hash_token,
    password_problems,
    verify_password,
)

router = APIRouter(tags=["auth"])

HOME_AFTER_LOGIN = "/professors"

# A single account may request at most this many verification mails per hour.
VERIFY_RESEND_LIMIT = 5


# ── helpers ─────────────────────────────────────────────────────


def _issue_token(db: Session, user: User, purpose: str, ttl_hours: int) -> str:
    """Create a single-use token; only its hash is stored."""
    raw = generate_token()
    db.add(
        EmailToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            purpose=purpose,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl_hours),
            created_at=datetime.now(timezone.utc),
        )
    )
    db.flush()
    return raw


def _consume_token(db: Session, raw: str, purpose: str) -> Optional[User]:
    token = db.execute(
        select(EmailToken).where(
            EmailToken.token_hash == hash_token(raw), EmailToken.purpose == purpose
        )
    ).scalar_one_or_none()
    if token is None or token.used_at is not None:
        return None
    expires = token.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return None
    token.used_at = datetime.now(timezone.utc)
    return db.get(User, token.user_id)


def _send_verification(db: Session, user: User) -> None:
    raw = _issue_token(
        db, user, EmailToken.PURPOSE_VERIFY, settings.email_verification_ttl_hours
    )
    url = f"{settings.base_url.rstrip('/')}/verify-email/{raw}"
    email_service.send_verification_email(user.email, user.username, url)


def _recent_token_count(db: Session, user_id: int, purpose: str, hours: int = 1) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return int(
        db.execute(
            select(func.count(EmailToken.id)).where(
                EmailToken.user_id == user_id,
                EmailToken.purpose == purpose,
                EmailToken.created_at >= since,
            )
        ).scalar_one()
    )


# ── register ────────────────────────────────────────────────────


@router.get("/register")
def register_form(request: Request, user=Depends(get_current_user)):
    if user:
        return RedirectResponse(HOME_AFTER_LOGIN, status_code=303)
    return render(request, "auth/register.html")


@router.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(""),
    full_name: str = Form(""),
    db: Session = Depends(get_db),
):
    values = {"username": username, "email": email, "full_name": full_name}

    try:
        payload = RegisterForm(
            username=username, email=email, password=password, full_name=full_name or None
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        return render(
            request,
            "auth/register.html",
            {"error": first["msg"].removeprefix("Value error, "), "values": values},
            status_code=400,
        )

    if password_confirm and password_confirm != password:
        return render(
            request,
            "auth/register.html",
            {"error": "The two passwords don't match.", "values": values},
            status_code=400,
        )

    problems = password_problems(password)
    if problems:
        return render(
            request,
            "auth/register.html",
            {"error": problems[0], "values": values},
            status_code=400,
        )

    taken = db.execute(
        select(User).where(
            (func.lower(User.username) == payload.username.lower())
            | (func.lower(User.email) == payload.email.lower())
        )
    ).scalar_one_or_none()
    if taken is not None:
        field = "username" if taken.username.lower() == payload.username.lower() else "email"
        return render(
            request,
            "auth/register.html",
            {"error": f"That {field} is already registered.", "values": values},
            status_code=400,
        )

    user = User(
        username=payload.username,
        email=payload.email.lower(),
        full_name=payload.full_name,
        password_hash=get_password_hash(password),
        is_email_verified=False,
        signup_ip=client_ip(request),
    )
    db.add(user)
    db.flush()

    _send_verification(db, user)
    log_activity(db, Action.REGISTER, user_id=user.id, request=request,
                 summary=f"{user.username} signed up")

    request.session["uid"] = user.id
    set_flash(
        request,
        f"Welcome, {user.username}. We sent a confirmation link to {user.email}.",
        "success",
    )
    return RedirectResponse("/verify-email", status_code=303)


# ── email verification ──────────────────────────────────────────


@router.get("/verify-email")
def verify_email_notice(request: Request, user: User = Depends(require_user)):
    if user.is_email_verified:
        return RedirectResponse(HOME_AFTER_LOGIN, status_code=303)
    return render(request, "auth/verify_email.html", {"email_enabled": settings.email_enabled})


@router.get("/verify-email/{token}")
def verify_email(request: Request, token: str, db: Session = Depends(get_db)):
    user = _consume_token(db, token, EmailToken.PURPOSE_VERIFY)
    if user is None:
        return render(
            request,
            "auth/verify_email.html",
            {
                "error": "That confirmation link is invalid or has expired. "
                "Sign in and request a new one.",
                "email_enabled": settings.email_enabled,
            },
            status_code=400,
        )

    if not user.is_email_verified:
        user.is_email_verified = True
        user.email_verified_at = datetime.now(timezone.utc)
        log_activity(db, Action.EMAIL_VERIFIED, user_id=user.id, request=request,
                     summary=f"{user.username} confirmed {user.email}")

    request.session["uid"] = user.id
    set_flash(request, "Email confirmed. You're all set.", "success")
    return RedirectResponse(HOME_AFTER_LOGIN, status_code=303)


@router.post("/verify-email/resend")
def resend_verification(
    request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    if user.is_email_verified:
        return RedirectResponse(HOME_AFTER_LOGIN, status_code=303)

    if _recent_token_count(db, user.id, EmailToken.PURPOSE_VERIFY) >= VERIFY_RESEND_LIMIT:
        set_flash(
            request,
            "You've requested several confirmation emails recently. Please wait a while "
            "before trying again, and check your spam folder.",
            "warning",
        )
        return RedirectResponse("/verify-email", status_code=303)

    _send_verification(db, user)
    log_activity(db, Action.VERIFY_RESENT, user_id=user.id, request=request)
    set_flash(request, f"Sent a fresh confirmation link to {user.email}.", "success")
    return RedirectResponse("/verify-email", status_code=303)


# ── login / logout ──────────────────────────────────────────────


@router.get("/login")
def login_form(request: Request, user=Depends(get_current_user), next: str = ""):
    if user:
        return RedirectResponse(HOME_AFTER_LOGIN, status_code=303)
    return render(request, "auth/login.html", {"next": next})


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        payload = LoginForm(username=username, password=password)
    except ValidationError:
        return render(
            request,
            "auth/login.html",
            {"error": "Enter your username and password."},
            status_code=400,
        )

    identifier = payload.username.lower()
    user = db.execute(
        select(User).where(
            (func.lower(User.username) == identifier) | (func.lower(User.email) == identifier)
        )
    ).scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        log_activity(
            db,
            Action.LOGIN_FAILED,
            user_id=user.id if user else None,
            request=request,
            summary=f"Failed sign-in for {payload.username!r}",
        )
        return render(
            request,
            "auth/login.html",
            {"error": "Invalid username or password.", "values": {"username": username}},
            status_code=401,
        )

    if not user.is_active:
        return render(
            request,
            "auth/login.html",
            {"error": "This account has been suspended. Contact support."},
            status_code=403,
        )

    now = datetime.now(timezone.utc)
    user.last_login_at = now
    user.last_seen_at = now
    request.session["uid"] = user.id
    log_activity(db, Action.LOGIN, user_id=user.id, request=request,
                 summary=f"{user.username} signed in")

    destination = next if next.startswith("/") and not next.startswith("//") else HOME_AFTER_LOGIN
    if not user.is_email_verified:
        destination = "/verify-email"
    return RedirectResponse(destination, status_code=303)


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if user:
        log_activity(db, Action.LOGOUT, user_id=user.id, request=request)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ── password reset ──────────────────────────────────────────────


@router.get("/forgot-password")
def forgot_password_form(request: Request):
    return render(request, "auth/forgot_password.html")


@router.post("/forgot-password")
def forgot_password(
    request: Request, email: str = Form(...), db: Session = Depends(get_db)
):
    user = db.execute(
        select(User).where(func.lower(User.email) == email.strip().lower())
    ).scalar_one_or_none()

    if user is not None and _recent_token_count(db, user.id, EmailToken.PURPOSE_RESET) < 5:
        raw = _issue_token(
            db, user, EmailToken.PURPOSE_RESET, settings.password_reset_ttl_hours
        )
        url = f"{settings.base_url.rstrip('/')}/reset-password/{raw}"
        email_service.send_password_reset_email(user.email, user.username, url)
        log_activity(db, Action.PASSWORD_RESET_REQUESTED, user_id=user.id, request=request)

    # Always the same response - never reveal which addresses have accounts.
    return render(
        request,
        "auth/forgot_password.html",
        {"sent": True, "email": email.strip()},
    )


@router.get("/reset-password/{token}")
def reset_password_form(request: Request, token: str, db: Session = Depends(get_db)):
    candidate = db.execute(
        select(EmailToken).where(
            EmailToken.token_hash == hash_token(token),
            EmailToken.purpose == EmailToken.PURPOSE_RESET,
        )
    ).scalar_one_or_none()
    expired = candidate is None or candidate.used_at is not None
    if not expired and candidate is not None:
        expires = candidate.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        expired = expires < datetime.now(timezone.utc)
    return render(
        request,
        "auth/reset_password.html",
        {"token": token, "expired": expired},
        status_code=400 if expired else 200,
    )


@router.post("/reset-password/{token}")
def reset_password(
    request: Request,
    token: str,
    password: str = Form(...),
    password_confirm: str = Form(""),
    db: Session = Depends(get_db),
):
    if password_confirm and password_confirm != password:
        return render(
            request,
            "auth/reset_password.html",
            {"token": token, "error": "The two passwords don't match."},
            status_code=400,
        )
    problems = password_problems(password)
    if problems:
        return render(
            request,
            "auth/reset_password.html",
            {"token": token, "error": problems[0]},
            status_code=400,
        )

    user = _consume_token(db, token, EmailToken.PURPOSE_RESET)
    if user is None:
        return render(
            request,
            "auth/reset_password.html",
            {"token": token, "expired": True},
            status_code=400,
        )

    user.password_hash = get_password_hash(password)
    log_activity(db, Action.PASSWORD_RESET, user_id=user.id, request=request,
                 summary=f"{user.username} reset their password")
    set_flash(request, "Password updated. You can sign in now.", "success")
    return RedirectResponse("/login", status_code=303)
