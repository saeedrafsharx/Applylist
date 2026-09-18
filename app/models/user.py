from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from .assistant import Conversation
    from .billing import Payment, Subscription
    from .contact import Contact
    from .position import Position


class User(Base, TimestampMixin):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    full_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    signup_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)

    contacts: Mapped[List["Contact"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    positions: Mapped[List["Position"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    # `foreign_keys` is required: Subscription points at user twice, via
    # `user_id` (the subscriber) and `granted_by_user_id` (the admin who granted it).
    subscriptions: Mapped[List["Subscription"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="Subscription.id.desc()",
        foreign_keys="Subscription.user_id",
    )
    payments: Mapped[List["Payment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", order_by="Payment.id.desc()"
    )
    conversations: Mapped[List["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    # ── subscription helpers ────────────────────────────────────
    @property
    def active_subscription(self) -> Optional["Subscription"]:
        for sub in self.subscriptions:
            if sub.is_active:
                return sub
        return None

    @property
    def is_paid(self) -> bool:
        return self.active_subscription is not None

    @property
    def plan_code(self) -> str:
        sub = self.active_subscription
        return sub.plan.code if sub and sub.plan else "free"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.id} {self.username!r}>"


class EmailToken(Base):
    """Single-use, hashed token for email verification and password reset."""

    __tablename__ = "email_token"
    __table_args__ = (Index("ix_email_token_user_purpose", "user_id", "purpose"),)

    PURPOSE_VERIFY = "verify_email"
    PURPOSE_RESET = "reset_password"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped["User"] = relationship()
