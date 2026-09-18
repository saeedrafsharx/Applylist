from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from .user import User


class CatalogUniversity(Base, TimestampMixin):
    __tablename__ = "catalog_university"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    professors: Mapped[List["CatalogProfessor"]] = relationship(
        back_populates="university", cascade="all, delete-orphan"
    )


class CatalogProfessor(Base, TimestampMixin):
    """
    A publicly listed faculty member, mirrored from a university's own
    directory page.

    Provenance is mandatory: `source_url` is the page the record came from and
    `scraped_at` is when it was last read, so every row stays auditable and a
    stale entry can be traced back and re-checked. `is_removed` implements
    takedown without destroying the audit trail.
    """

    __tablename__ = "catalog_professor"
    __table_args__ = (
        UniqueConstraint("university_id", "name", "email", name="uq_catalog_prof_identity"),
        Index("ix_catalog_prof_published", "is_published", "is_removed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    university_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_university.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    department: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    research_focus: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    profile_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # provenance
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    source_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    scraped_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_removed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    removed_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    removed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    university: Mapped["CatalogUniversity"] = relationship(back_populates="professors")

    @property
    def is_visible(self) -> bool:
        return self.is_published and not self.is_removed


class CatalogJobTitle(Base, TimestampMixin):
    """Reference list of role titles users can search when framing an application."""

    __tablename__ = "catalog_job_title"
    __table_args__ = (UniqueConstraint("title", "field", name="uq_job_title_field"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    field: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    level: Mapped[Optional[str]] = mapped_column(String(60), nullable=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    typical_requirements: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ScrapeSource(Base, TimestampMixin):
    """A configured directory page plus the parser that understands it."""

    __tablename__ = "scrape_source"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    parser: Mapped[str] = mapped_column(String(80), nullable=False)
    start_url: Mapped[str] = mapped_column(String(500), nullable=False)
    university_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ScrapeRun(Base):
    __tablename__ = "scrape_run"

    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_BLOCKED = "blocked_by_robots"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=STATUS_RUNNING)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    pages_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    log: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    triggered_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    triggered_by: Mapped[Optional["User"]] = relationship()


class TakedownRequest(Base):
    """A request from a listed person (or admin) to remove their catalog entry."""

    __tablename__ = "takedown_request"

    STATUS_OPEN = "open"
    STATUS_ACTIONED = "actioned"
    STATUS_REJECTED = "rejected"

    id: Mapped[int] = mapped_column(primary_key=True)
    professor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("catalog_professor.id", ondelete="SET NULL"), nullable=True
    )
    requester_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    subject_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=STATUS_OPEN)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    handled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    handled_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    professor: Mapped[Optional["CatalogProfessor"]] = relationship()
