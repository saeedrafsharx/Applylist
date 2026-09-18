from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from .user import User


class Plan(Base, TimestampMixin):
    __tablename__ = "plan"

    CODE_FREE = "free"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Zarinpal quotes amounts in Rial; 0 means free.
    price_rial: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # None = never expires (lifetime).
    duration_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    grants_catalog: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    grants_ai: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ai_monthly_quota: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    subscriptions: Mapped[List["Subscription"]] = relationship(back_populates="plan")

    @property
    def is_free(self) -> bool:
        return self.price_rial <= 0

    @property
    def price_toman(self) -> int:
        return self.price_rial // 10


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscription"
    __table_args__ = (Index("ix_subscription_user_status", "user_id", "status"),)

    STATUS_ACTIVE = "active"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELED = "canceled"

    SOURCE_MANUAL = "manual"
    SOURCE_ZARINPAL = "zarinpal"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan.id", ondelete="RESTRICT"), nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default=STATUS_ACTIVE)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default=SOURCE_MANUAL)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # None = lifetime.
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    canceled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    granted_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="subscriptions", foreign_keys=[user_id])
    plan: Mapped["Plan"] = relationship(back_populates="subscriptions")

    @property
    def is_active(self) -> bool:
        if self.status != self.STATUS_ACTIVE:
            return False
        if self.expires_at is None:
            return True
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > datetime.now(timezone.utc)

    @property
    def days_remaining(self) -> Optional[int]:
        if self.expires_at is None:
            return None
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        delta = expires - datetime.now(timezone.utc)
        return max(0, delta.days)


class Payment(Base, TimestampMixin):
    __tablename__ = "payment"
    __table_args__ = (Index("ix_payment_user_status", "user_id", "status"),)

    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"
    STATUS_CANCELED = "canceled"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan.id", ondelete="RESTRICT"), nullable=False)
    subscription_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("subscription.id", ondelete="SET NULL"), nullable=True
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="zarinpal")
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="IRR")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=STATUS_PENDING)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Zarinpal handles
    authority: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True, unique=True, index=True
    )
    ref_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    card_pan: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_request: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    raw_response: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    user: Mapped["User"] = relationship(back_populates="payments")
    plan: Mapped["Plan"] = relationship()

    @property
    def amount_toman(self) -> int:
        return self.amount // 10
