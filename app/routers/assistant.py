from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import RequirePlan, render, require_verified, set_flash, subscription_context
from ..models import Contact, Conversation, User
from ..services import ai as ai_service
from ..services.audit import Action, log_activity
from ..services.billing import user_has_feature

router = APIRouter(prefix="/assistant", tags=["assistant"])

require_ai = RequirePlan("ai")


def _conversations(db: Session, user: User) -> list[Conversation]:
    return list(
        db.execute(
            select(Conversation)
            .where(Conversation.user_id == user.id, Conversation.archived.is_(False))
            .order_by(Conversation.updated_at.desc())
            .limit(50)
        ).scalars()
    )


@router.get("")
def assistant_home(
    request: Request,
    user: User = Depends(require_verified),
    db: Session = Depends(get_db),
):
    """Gate page: the pitch for free users, the latest chat for paid ones."""
    if not user_has_feature(db, user, "ai"):
        log_activity(
            db, Action.AI_BLOCKED, user_id=user.id, request=request,
            summary="Viewed the assistant paywall",
        )
        return render(
            request,
            "assistant/locked.html",
            {**subscription_context(db, user)},
        )

    latest = db.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.archived.is_(False))
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest is None:
        return RedirectResponse("/assistant/new", status_code=303)
    return RedirectResponse(f"/assistant/c/{latest.id}", status_code=303)


@router.get("/new")
def new_conversation(
    request: Request,
    kind: str = Conversation.KIND_CHAT,
    user: User = Depends(require_ai),
    db: Session = Depends(get_db),
):
    conversation = Conversation(
        user_id=user.id,
        title="New conversation",
        kind=kind if kind in (Conversation.KIND_CHAT, Conversation.KIND_EMAIL) else
        Conversation.KIND_CHAT,
    )
    db.add(conversation)
    db.flush()
    return RedirectResponse(f"/assistant/c/{conversation.id}", status_code=303)


@router.get("/c/{conversation_id}")
def view_conversation(
    request: Request,
    conversation_id: int,
    user: User = Depends(require_ai),
    db: Session = Depends(get_db),
):
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        return RedirectResponse("/assistant", status_code=303)

    contacts = list(
        db.execute(
            select(Contact).where(Contact.owner_id == user.id).order_by(Contact.name)
        ).scalars()
    )

    return render(
        request,
        "assistant/chat.html",
        {
            "conversation": conversation,
            "conversations": _conversations(db, user),
            "messages": conversation.messages,
            "contacts": contacts,
            "quota": ai_service.quota_for(db, user),
            "used": ai_service.get_usage(db, user.id).message_count,
            "ai_configured": settings.ai_enabled,
            **subscription_context(db, user),
        },
    )


@router.post("/c/{conversation_id}/send")
def send_message(
    request: Request,
    conversation_id: int,
    message: str = Form(...),
    user: User = Depends(require_ai),
    db: Session = Depends(get_db),
):
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        return RedirectResponse("/assistant", status_code=303)

    try:
        reply = ai_service.send_message(db, user, conversation, message)
    except ai_service.QuotaExceeded as exc:
        log_activity(db, Action.AI_QUOTA_EXCEEDED, user_id=user.id, request=request)
        set_flash(request, str(exc), "warning")
        return RedirectResponse(f"/assistant/c/{conversation_id}", status_code=303)
    except ai_service.AIError as exc:
        set_flash(request, str(exc), "error")
        return RedirectResponse(f"/assistant/c/{conversation_id}", status_code=303)

    log_activity(
        db, Action.AI_MESSAGE, user_id=user.id, request=request,
        target_type="conversation", target_id=conversation.id,
        summary=f"Assistant reply ({reply.output_tokens} output tokens)",
        detail={
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
            "model": reply.model,
            "latency_ms": reply.latency_ms,
        },
    )
    return RedirectResponse(f"/assistant/c/{conversation_id}#latest", status_code=303)


@router.post("/c/{conversation_id}/archive")
def archive_conversation(
    request: Request,
    conversation_id: int,
    user: User = Depends(require_ai),
    db: Session = Depends(get_db),
):
    conversation = db.get(Conversation, conversation_id)
    if conversation is not None and conversation.user_id == user.id:
        conversation.archived = True
        set_flash(request, "Conversation archived.", "info")
    return RedirectResponse("/assistant", status_code=303)


@router.get("/draft")
def draft_form(
    request: Request,
    contact_id: int = 0,
    user: User = Depends(require_ai),
    db: Session = Depends(get_db),
):
    contacts = list(
        db.execute(
            select(Contact).where(Contact.owner_id == user.id).order_by(Contact.name)
        ).scalars()
    )
    return render(
        request,
        "assistant/draft.html",
        {
            "contacts": contacts,
            "selected_id": contact_id,
            "quota": ai_service.quota_for(db, user),
            "used": ai_service.get_usage(db, user.id).message_count,
            **subscription_context(db, user),
        },
    )


@router.post("/draft")
def create_draft(
    request: Request,
    contact_id: int = Form(...),
    purpose: str = Form("first_contact"),
    tone: str = Form("professional and warm"),
    extra: str = Form(""),
    user: User = Depends(require_ai),
    db: Session = Depends(get_db),
):
    contact = db.get(Contact, contact_id)
    if contact is None or contact.owner_id != user.id:
        set_flash(request, "Pick one of your own contacts.", "error")
        return RedirectResponse("/assistant/draft", status_code=303)

    conversation = Conversation(
        user_id=user.id,
        title=f"Email to {contact.name}",
        kind=Conversation.KIND_EMAIL,
    )
    db.add(conversation)
    db.flush()

    prompt = ai_service.build_email_prompt(
        contact, purpose=purpose, tone=tone, extra=extra, user=user
    )

    try:
        reply = ai_service.send_message(db, user, conversation, prompt)
    except ai_service.AIError as exc:
        db.delete(conversation)
        set_flash(request, str(exc), "error")
        return RedirectResponse(f"/assistant/draft?contact_id={contact_id}", status_code=303)

    log_activity(
        db, Action.AI_MESSAGE, user_id=user.id, request=request,
        target_type="conversation", target_id=conversation.id,
        summary=f"Drafted an email to {contact.name}",
        detail={"purpose": purpose, "output_tokens": reply.output_tokens},
    )
    conversation.updated_at = datetime.now(timezone.utc)
    return RedirectResponse(f"/assistant/c/{conversation.id}#latest", status_code=303)
