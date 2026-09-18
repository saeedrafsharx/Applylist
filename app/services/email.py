from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Optional

from ..config import settings

log = logging.getLogger(__name__)


class EmailError(RuntimeError):
    pass


def _build(to: str, subject: str, text: str, html: Optional[str] = None) -> EmailMessage:
    msg = EmailMessage()
    name, addr = parseaddr(settings.smtp_from)
    msg["From"] = formataddr((name, addr)) if name else addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def send_email(to: str, subject: str, text: str, html: Optional[str] = None) -> bool:
    """
    Send one message. Returns True if it was handed to the SMTP server.

    With SMTP unconfigured (local dev), the message is logged instead of sent so
    signup flows stay usable without a mail server.
    """
    if not settings.email_enabled:
        log.warning(
            "SMTP not configured - email to %s not sent.\nSubject: %s\n%s", to, subject, text
        )
        return False

    msg = _build(to, subject, text, html)
    try:
        if settings.smtp_ssl:
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
        with server:
            server.ehlo()
            if settings.smtp_starttls and not settings.smtp_ssl:
                server.starttls()
                server.ehlo()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        log.info("Sent %r to %s", subject, to)
        return True
    except Exception as exc:  # noqa: BLE001 - never let mail failure break a request
        log.exception("Failed to send email to %s: %s", to, exc)
        return False


# ── templated messages ──────────────────────────────────────────

_BUTTON = (
    'style="display:inline-block;padding:12px 22px;border-radius:12px;'
    'background:linear-gradient(90deg,#0284c7,#c026d3);color:#fff;'
    'text-decoration:none;font-weight:600"'
)


def _wrap(title: str, body_html: str) -> str:
    return f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f8fafc;font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#0f172a">
  <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:16px;padding:32px;border:1px solid #e2e8f0">
    <h1 style="margin:0 0 16px;font-size:20px;background:linear-gradient(90deg,#0284c7,#c026d3);-webkit-background-clip:text;background-clip:text;color:transparent">{title}</h1>
    {body_html}
    <p style="margin-top:28px;font-size:12px;color:#64748b">ApplyList · applylist.ir</p>
  </div>
</body></html>"""


def send_verification_email(to: str, username: str, verify_url: str) -> bool:
    subject = "Verify your ApplyList email"
    text = (
        f"Hi {username},\n\n"
        f"Confirm your email address to finish setting up your ApplyList account:\n\n"
        f"{verify_url}\n\n"
        f"This link expires in {settings.email_verification_ttl_hours} hours.\n"
        f"If you didn't sign up, you can ignore this message.\n"
    )
    html = _wrap(
        "Confirm your email",
        f"<p>Hi {username},</p>"
        f"<p>Confirm your email address to finish setting up your ApplyList account.</p>"
        f'<p style="margin:24px 0"><a href="{verify_url}" {_BUTTON}>Verify email</a></p>'
        f'<p style="font-size:13px;color:#475569">Or paste this link into your browser:<br>'
        f'<a href="{verify_url}">{verify_url}</a></p>'
        f'<p style="font-size:13px;color:#475569">This link expires in '
        f"{settings.email_verification_ttl_hours} hours. If you didn't sign up, ignore this email.</p>",
    )
    return send_email(to, subject, text, html)


def send_password_reset_email(to: str, username: str, reset_url: str) -> bool:
    subject = "Reset your ApplyList password"
    text = (
        f"Hi {username},\n\n"
        f"Reset your ApplyList password here:\n\n{reset_url}\n\n"
        f"This link expires in {settings.password_reset_ttl_hours} hours.\n"
        f"If you didn't ask for this, no action is needed.\n"
    )
    html = _wrap(
        "Reset your password",
        f"<p>Hi {username},</p>"
        f'<p style="margin:24px 0"><a href="{reset_url}" {_BUTTON}>Reset password</a></p>'
        f'<p style="font-size:13px;color:#475569">Expires in '
        f"{settings.password_reset_ttl_hours} hours. If you didn't ask for this, ignore it.</p>",
    )
    return send_email(to, subject, text, html)


def send_subscription_receipt(to: str, username: str, plan_name: str, ref_id: str,
                              amount_toman: int, expires: Optional[str]) -> bool:
    subject = f"Your ApplyList {plan_name} subscription is active"
    window = f"Valid until {expires}." if expires else "Valid for life."
    text = (
        f"Hi {username},\n\nYour {plan_name} subscription is active. {window}\n"
        f"Amount: {amount_toman:,} Toman\nReference: {ref_id}\n\nThanks for supporting ApplyList.\n"
    )
    html = _wrap(
        f"{plan_name} is active",
        f"<p>Hi {username},</p><p>Your <strong>{plan_name}</strong> subscription is active. {window}</p>"
        f'<table style="font-size:14px;margin:20px 0">'
        f'<tr><td style="padding:4px 16px 4px 0;color:#475569">Amount</td><td>{amount_toman:,} Toman</td></tr>'
        f'<tr><td style="padding:4px 16px 4px 0;color:#475569">Reference</td><td>{ref_id}</td></tr>'
        f"</table><p>Thanks for supporting ApplyList.</p>",
    )
    return send_email(to, subject, text, html)
