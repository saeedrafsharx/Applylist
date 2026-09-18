from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from .user import User


class Contact(Base, TimestampMixin):
    """A professor / lab contact the user is tracking."""

    __tablename__ = "contact"
    __table_args__ = (
        Index("ix_contact_owner_category", "owner_id", "category"),
        Index("ix_contact_owner_name", "owner_id", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    university: Mapped[str] = mapped_column(String(200), nullable=False)
    research_focus: Mapped[str] = mapped_column(String(500), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(320), nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False, default="#")
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="General")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    email_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Set when the row was copied out of the shared catalog.
    catalog_professor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("catalog_professor.id", ondelete="SET NULL"), nullable=True
    )

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner: Mapped["User"] = relationship(back_populates="contacts")
