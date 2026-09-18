"""Idempotent startup data: plans, the bootstrap admin, and reference rows."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import CatalogJobTitle, Plan, ScrapeSource, User
from .services.security import get_password_hash

log = logging.getLogger(__name__)

DEFAULT_PLANS = [
    dict(
        code="free",
        name="Free",
        description="Track your own contacts and positions. Unlimited entries, CSV import/export.",
        price_rial=0,
        duration_days=None,
        grants_catalog=False,
        grants_ai=False,
        ai_monthly_quota=0,
        sort_order=0,
    ),
    dict(
        code="pro_monthly",
        name="Pro · Monthly",
        description="Everything in Free, plus the contact database and the AI assistant.",
        price_rial=990_000,  # 99,000 Toman
        duration_days=30,
        grants_catalog=True,
        grants_ai=True,
        ai_monthly_quota=300,
        sort_order=1,
    ),
    dict(
        code="pro_yearly",
        name="Pro · Yearly",
        description="A full application season of database access and AI help. Two months free.",
        price_rial=9_900_000,  # 990,000 Toman
        duration_days=365,
        grants_catalog=True,
        grants_ai=True,
        ai_monthly_quota=600,
        sort_order=2,
    ),
]

# Reference job titles. These are role *names*, not contact data.
DEFAULT_JOB_TITLES = [
    ("PhD Student", "Neuroscience", "Graduate"),
    ("PhD Student", "Machine Learning", "Graduate"),
    ("PhD Student", "Computer Science", "Graduate"),
    ("MSc Student (Thesis)", "Neuroscience", "Graduate"),
    ("MSc Student (Thesis)", "Computer Science", "Graduate"),
    ("Research Assistant", "Neuroscience", "Entry"),
    ("Research Assistant", "Machine Learning", "Entry"),
    ("Graduate Research Assistant (GRA)", "General", "Graduate"),
    ("Teaching Assistant (TA)", "General", "Graduate"),
    ("Postdoctoral Fellow", "Neuroscience", "Postdoc"),
    ("Postdoctoral Fellow", "Machine Learning", "Postdoc"),
    ("Research Technician", "Neuroscience", "Entry"),
    ("Lab Manager", "Neuroscience", "Mid"),
    ("Data Scientist", "Machine Learning", "Industry"),
    ("Research Scientist", "Machine Learning", "Industry"),
    ("Machine Learning Engineer", "Machine Learning", "Industry"),
    ("Research Intern", "Machine Learning", "Internship"),
    ("Visiting Researcher", "General", "Mid"),
    ("Summer Research Student", "General", "Internship"),
]

# Directory pages to crawl. Disabled by default: an admin enables and runs each
# one deliberately, after checking it is still the right page.
DEFAULT_SCRAPE_SOURCES = [
    dict(
        key="mcgill_ipn",
        label="McGill — Integrated Program in Neuroscience",
        parser="auto",
        start_url="https://www.mcgill.ca/ipn/prospective/supervisors-recruiting",
        university_slug="mcgill",
        notes="Public table of IPN supervisors currently recruiting; addresses written as name [at] mcgill.ca.",
    ),
    dict(
        key="queens_cns",
        label="Queen's — Centre for Neuroscience Studies",
        parser="auto",
        start_url="https://www.queensu.ca/neuroscience/people",
        university_slug="queens",
        notes="Paginated member directory with addresses and research interests. The site asks for Crawl-delay: 10.",
    ),
]

# Seeded rows that shipped with a value that has since stopped working. A row
# still holding the old value is updated; one an admin has changed is left alone.
SCRAPE_SOURCE_REPAIRS = {
    "mcgill_ipn": {"parser": ("mailto_directory", "auto")},
    "queens_cns": {
        "parser": ("profile_links", "auto"),
        "start_url": ("https://neuroscience.queensu.ca/research/faculty",
                      "https://www.queensu.ca/neuroscience/people"),
    },
}

DEFAULT_UNIVERSITIES = [
    dict(slug="mcgill", name="McGill University", country="Canada", city="Montreal",
         website="https://www.mcgill.ca"),
    dict(slug="queens", name="Queen's University", country="Canada", city="Kingston",
         website="https://www.queensu.ca"),
]


def seed(db: Session) -> None:
    _seed_plans(db)
    _seed_universities(db)
    _seed_job_titles(db)
    _seed_scrape_sources(db)
    _seed_admin(db)


def _seed_plans(db: Session) -> None:
    for spec in DEFAULT_PLANS:
        plan = db.execute(select(Plan).where(Plan.code == spec["code"])).scalar_one_or_none()
        if plan is None:
            db.add(Plan(**spec, is_active=True))
            log.info("Created plan %s", spec["code"])
    db.flush()


def _seed_universities(db: Session) -> None:
    from .models import CatalogUniversity

    for spec in DEFAULT_UNIVERSITIES:
        existing = db.execute(
            select(CatalogUniversity).where(CatalogUniversity.slug == spec["slug"])
        ).scalar_one_or_none()
        if existing is None:
            db.add(CatalogUniversity(**spec))
    db.flush()


def _seed_job_titles(db: Session) -> None:
    if db.execute(select(func.count(CatalogJobTitle.id))).scalar_one():
        return
    for title, field, level in DEFAULT_JOB_TITLES:
        db.add(CatalogJobTitle(title=title, field=field, level=level, is_published=True))
    log.info("Seeded %d job titles", len(DEFAULT_JOB_TITLES))
    db.flush()


def _seed_scrape_sources(db: Session) -> None:
    for spec in DEFAULT_SCRAPE_SOURCES:
        existing = db.execute(
            select(ScrapeSource).where(ScrapeSource.key == spec["key"])
        ).scalar_one_or_none()
        if existing is None:
            db.add(ScrapeSource(**spec, enabled=False))
            continue
        for field, (old, new) in SCRAPE_SOURCE_REPAIRS.get(spec["key"], {}).items():
            if getattr(existing, field) == old:
                setattr(existing, field, new)
                if field == "start_url":
                    existing.notes = spec["notes"]
    db.flush()


def _seed_admin(db: Session) -> None:
    """
    Promote or create the bootstrap admin from ADMIN_EMAIL / ADMIN_PASSWORD.

    Creating an admin only happens when both are set. An existing account with
    that address is promoted rather than duplicated, and its password is left
    alone — this must never become a way to reset a live account's credentials.
    """
    if not settings.admin_email:
        return

    existing = db.execute(
        select(User).where(func.lower(User.email) == settings.admin_email.lower())
    ).scalar_one_or_none()

    if existing is not None:
        if not existing.is_admin:
            existing.is_admin = True
            log.info("Promoted %s to admin", existing.username)
        if not existing.is_email_verified:
            existing.is_email_verified = True
            existing.email_verified_at = datetime.now(timezone.utc)
        return

    if not settings.admin_password:
        log.warning(
            "ADMIN_EMAIL is set but ADMIN_PASSWORD is not - no admin account created."
        )
        return

    db.add(
        User(
            username=settings.admin_username,
            email=settings.admin_email.lower(),
            password_hash=get_password_hash(settings.admin_password),
            is_admin=True,
            is_active=True,
            is_email_verified=True,
            email_verified_at=datetime.now(timezone.utc),
            full_name="Administrator",
        )
    )
    log.info("Created bootstrap admin %s", settings.admin_email)
    db.flush()
