"""SQLAlchemy models. Import every module here so Alembic autogenerate sees them."""

from .assistant import AIMessage, AIUsage, Conversation
from .audit import ActivityLog
from .base import Base, TimestampMixin, utcnow
from .billing import Payment, Plan, Subscription
from .catalog import (
    CatalogJobTitle,
    CatalogProfessor,
    CatalogUniversity,
    ScrapeRun,
    ScrapeSource,
    TakedownRequest,
)
from .contact import Contact
from .position import Position
from .user import EmailToken, User

__all__ = [
    "AIMessage",
    "AIUsage",
    "ActivityLog",
    "Base",
    "CatalogJobTitle",
    "CatalogProfessor",
    "CatalogUniversity",
    "Contact",
    "Conversation",
    "EmailToken",
    "Payment",
    "Plan",
    "Position",
    "ScrapeRun",
    "ScrapeSource",
    "Subscription",
    "TakedownRequest",
    "TimestampMixin",
    "User",
    "utcnow",
]
