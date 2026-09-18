from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AIMessage, AIUsage, Contact, Conversation, Position, User

log = logging.getLogger(__name__)

try:  # the app must still boot (and admin must still work) without the SDK
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]


class AIError(RuntimeError):
    """Message is safe to show the user."""


class QuotaExceeded(AIError):
    pass


SYSTEM_PROMPT = """You are the ApplyList assistant. You help students applying to graduate \
programs (Master's, PhD), research positions, and academic or industry jobs. Many of your users \
are international applicants — often writing in English as a second language — applying from Iran \
and elsewhere to institutions in Canada, the US, and Europe.

What you help with:
- Drafting and revising cold outreach emails to professors and labs
- Follow-up and reminder emails when a first email got no reply
- Statements of purpose, research statements, and CV bullet points
- Explaining how admissions, funding, RA/TA positions, and supervisor matching work
- Reviewing the user's draft and giving specific, line-level suggestions

How to write outreach emails:
- Subject lines that are specific, not generic ("PhD application — computational neuroimaging, \
Fall 2027" beats "Prospective student inquiry")
- Under 200 words. Professors skim.
- Open with a concrete reason for contacting *this* person — a paper, a method, a finding
- One short paragraph on the student's relevant background, with specifics over adjectives
- A clear, small ask: is the lab recruiting, may I send a CV
- Plain, direct English. No flattery, no "I am writing to express my profound interest"
- Never invent the student's publications, grades, scores, or experience. If you need a detail you \
don't have, leave a clearly marked placeholder like [your GPA] and say what's missing.

General rules:
- Be concrete and honest. If a plan is weak — a vague email, an unrealistic timeline, a mismatched \
supervisor — say so plainly and say what would be better.
- Don't guarantee outcomes, invent deadlines, funding amounts, or admission requirements. If \
something depends on a specific program's current rules, say the user should confirm on the \
department page.
- Match the user's language. If they write in Persian, reply in Persian.
- Format for reading: short paragraphs, and a copyable block for anything meant to be sent."""


@dataclass(slots=True)
class AIReply:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    latency_ms: int


def _client():
    if anthropic is None:
        raise AIError("The AI assistant isn't installed on this server.")
    if not settings.ai_enabled:
        raise AIError("The AI assistant isn't configured yet. Please contact support.")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def get_usage(db: Session, user_id: int, period: Optional[str] = None) -> AIUsage:
    period = period or current_period()
    usage = db.execute(
        select(AIUsage).where(AIUsage.user_id == user_id, AIUsage.period == period)
    ).scalar_one_or_none()
    if usage is None:
        usage = AIUsage(user_id=user_id, period=period)
        db.add(usage)
        db.flush()
    return usage


def quota_for(db: Session, user: User) -> int:
    from .billing import active_subscription

    if user.is_admin:
        return 10_000
    sub = active_subscription(db, user.id)
    if sub and sub.plan and sub.plan.ai_monthly_quota:
        return sub.plan.ai_monthly_quota
    return settings.ai_monthly_message_quota


def remaining_quota(db: Session, user: User) -> int:
    return max(0, quota_for(db, user) - get_usage(db, user.id).message_count)


def _user_context(db: Session, user: User) -> str:
    """A compact snapshot of the user's own tracked data, for grounding."""
    contacts = list(
        db.execute(
            select(Contact).where(Contact.owner_id == user.id).order_by(Contact.name).limit(40)
        ).scalars()
    )
    positions = list(
        db.execute(
            select(Position).where(Position.owner_id == user.id).order_by(Position.field).limit(25)
        ).scalars()
    )

    lines = [f"The user's display name is {user.full_name or user.username}."]
    if contacts:
        lines.append(f"\nContacts they are tracking ({len(contacts)}):")
        for c in contacts:
            state = "emailed" if c.email_sent else "not emailed yet"
            lines.append(f"- {c.name} — {c.university} — {c.research_focus} — {state}")
    else:
        lines.append("\nThey have not added any contacts yet.")

    if positions:
        lines.append(f"\nPositions they are tracking ({len(positions)}):")
        for p in positions:
            deadline = f", deadline {p.deadline.isoformat()}" if p.deadline else ""
            lines.append(f"- {p.field} — status {p.status}{deadline}")

    lines.append(
        "\nUse this only when it is relevant to what they asked. Do not recite it back at them."
    )
    return "\n".join(lines)


def _history(db: Session, conversation: Conversation) -> list[dict]:
    turns = settings.ai_history_turns * 2
    msgs = [m for m in conversation.messages if not m.error]
    out: list[dict] = []
    for m in msgs[-turns:]:
        out.append({"role": m.role, "content": m.content})
    # The API requires the first message to be from the user.
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def send_message(
    db: Session,
    user: User,
    conversation: Conversation,
    user_text: str,
) -> AIMessage:
    """
    Append the user's turn, call Claude, persist and return the reply.

    Raises QuotaExceeded before spending anything if the user is out of quota.
    """
    user_text = (user_text or "").strip()
    if not user_text:
        raise AIError("Write a message first.")
    if len(user_text) > 20_000:
        raise AIError("That message is too long. Please shorten it.")

    quota = quota_for(db, user)
    usage = get_usage(db, user.id)
    if usage.message_count >= quota:
        raise QuotaExceeded(
            f"You've used all {quota} assistant messages for this month. "
            "Your quota resets at the start of next month."
        )

    now = datetime.now(timezone.utc)
    db.add(
        AIMessage(
            conversation_id=conversation.id,
            role=AIMessage.ROLE_USER,
            content=user_text,
            created_at=now,
        )
    )
    db.flush()
    db.refresh(conversation)

    messages = _history(db, conversation)
    if not messages:
        messages = [{"role": "user", "content": user_text}]

    client = _client()
    started = time.monotonic()
    try:
        with client.messages.stream(
            model=settings.anthropic_model,
            max_tokens=settings.ai_max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": _user_context(db, user)},
            ],
            thinking={"type": "adaptive"},
            output_config={"effort": settings.ai_effort},
            messages=messages,
        ) as stream:
            response = stream.get_final_message()
    except anthropic.NotFoundError as exc:
        log.exception("Claude model not found")
        raise AIError("The assistant is misconfigured (unknown model).") from exc
    except anthropic.AuthenticationError as exc:
        log.exception("Claude auth failed")
        raise AIError("The assistant isn't authenticated. Please contact support.") from exc
    except anthropic.RateLimitError as exc:
        raise AIError("The assistant is busy right now. Please try again in a moment.") from exc
    except anthropic.APIStatusError as exc:
        log.exception("Claude API error %s", exc.status_code)
        if exc.status_code >= 500:
            raise AIError("The assistant is temporarily unavailable. Try again shortly.") from exc
        raise AIError("The assistant couldn't process that request.") from exc
    except anthropic.APIConnectionError as exc:
        log.exception("Claude connection error")
        raise AIError("Couldn't reach the assistant. Check the server's connection.") from exc

    latency_ms = int((time.monotonic() - started) * 1000)

    if response.stop_reason == "refusal":
        text = (
            "I can't help with that request. If you think this is a mistake, "
            "try rephrasing what you need."
        )
    else:
        text = "\n\n".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
    if not text:
        text = "I didn't manage to produce a reply. Please try rephrasing."

    reply = AIMessage(
        conversation_id=conversation.id,
        role=AIMessage.ROLE_ASSISTANT,
        content=text,
        model=response.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        latency_ms=latency_ms,
        created_at=datetime.now(timezone.utc),
    )
    db.add(reply)

    usage.message_count += 1
    usage.input_tokens += response.usage.input_tokens
    usage.output_tokens += response.usage.output_tokens

    if conversation.title == "New conversation":
        conversation.title = user_text[:80] + ("…" if len(user_text) > 80 else "")
    conversation.updated_at = datetime.now(timezone.utc)

    db.flush()
    return reply


def build_email_prompt(
    contact: Contact,
    *,
    purpose: str,
    tone: str,
    extra: str,
    user: User,
) -> str:
    """Turn the 'draft an email' form into one clear instruction."""
    purposes = {
        "first_contact": "a first cold outreach email introducing the student",
        "follow_up": "a polite follow-up to an earlier email that received no reply",
        "thank_you": "a short thank-you after the professor replied",
        "application_update": "a brief update that the student has submitted their application",
    }
    lines = [
        f"Draft {purposes.get(purpose, purposes['first_contact'])}.",
        "",
        "Recipient:",
        f"- Name: {contact.name}",
        f"- University: {contact.university}",
        f"- Research focus: {contact.research_focus}",
    ]
    if contact.source_url and contact.source_url != "#":
        lines.append(f"- Profile/source: {contact.source_url}")
    if contact.notes:
        lines.append(f"- Notes the student kept: {contact.notes}")
    lines += [
        "",
        f"Sender: {user.full_name or user.username}",
        f"Tone: {tone}",
    ]
    if extra.strip():
        lines += ["", "The student adds:", extra.strip()]
    lines += [
        "",
        "Give a subject line and the body. Use [square-bracket placeholders] for anything "
        "about the student you don't actually know — never invent their background.",
    ]
    return "\n".join(lines)
