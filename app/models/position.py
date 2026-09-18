from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from .user import User


class Position(Base, TimestampMixin):
    """An opening the user is tracking: PhD call, fellowship, RA post, job."""

    __tablename__ = "position"
    __table_args__ = (Index("ix_position_owner_category", "owner_id", "category"),)

    STATUSES = ("interested", "applied", "interview", "offer", "rejected", "archived")

    id: Mapped[int] = mapped_column(primary_key=True)
    field: Mapped[str] = mapped_column(String(300), nullable=False)
    link: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="General")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="interested")
    deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner: Mapped["User"] = relationship(back_populates="positions")
