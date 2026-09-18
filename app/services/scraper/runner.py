from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import CatalogProfessor, CatalogUniversity, ScrapeRun, ScrapeSource
from .fetcher import FetchError, PoliteFetcher, RobotsDisallowed
from .parsers import PARSERS, ParsedProfessor, enrich_from_profile

log = logging.getLogger(__name__)

# How many individual profile pages one run may follow, on top of the index.
MAX_PROFILE_FOLLOWS = 25


def run_source(
    db: Session,
    source: ScrapeSource,
    *,
    triggered_by_user_id: Optional[int] = None,
    follow_profiles: bool = True,
    dry_run: bool = False,
) -> ScrapeRun:
    """
    Fetch one configured directory and upsert what it publishes.

    Every run is recorded — including the ones that fetch nothing — so a parser
    that silently stops matching shows up in the admin list as a run with zero
    records rather than as nothing at all.
    """
    started = datetime.now(timezone.utc)
    clock = time.monotonic()
    run = ScrapeRun(
        source_key=source.key,
        status=ScrapeRun.STATUS_RUNNING,
        started_at=started,
        triggered_by_user_id=triggered_by_user_id,
    )
    db.add(run)
    db.flush()

    notes: list[str] = []
    records: list[ParsedProfessor] = []

    try:
        parser = PARSERS.get(source.parser)
        if parser is None:
            raise FetchError(f"Unknown parser {source.parser!r}")

        university = _university_for(db, source)

        with PoliteFetcher() as fetcher:
            if not fetcher.can_fetch(source.start_url):
                run.status = ScrapeRun.STATUS_BLOCKED
                run.error = (
                    f"robots.txt at {source.start_url} disallows our crawler. "
                    "Nothing was fetched."
                )
                _finish(run, clock, notes)
                return run

            html = fetcher.get(source.start_url)
            records = parser(html, source.start_url)
            notes.append(f"index: {len(records)} candidate records")

            if follow_profiles:
                needs_email = [r for r in records if not r.email and r.profile_url]
                for record in needs_email[:MAX_PROFILE_FOLLOWS]:
                    try:
                        profile_html = fetcher.get(record.profile_url)  # type: ignore[arg-type]
                        enrich_from_profile(record, profile_html)
                    except RobotsDisallowed:
                        notes.append(f"robots: skipped profile {record.profile_url}")
                    except FetchError as exc:
                        notes.append(f"skip {record.profile_url}: {exc}")

            run.pages_fetched = fetcher.pages_fetched

        usable = [r for r in records if r.email or r.profile_url]
        run.records_found = len(usable)

        if not dry_run:
            created, updated = _upsert(db, university, source.key, usable)
            run.records_created = created
            run.records_updated = updated
        else:
            notes.append("dry run - nothing written")

        source.last_run_at = started
        run.status = ScrapeRun.STATUS_SUCCESS

    except RobotsDisallowed as exc:
        run.status = ScrapeRun.STATUS_BLOCKED
        run.error = str(exc)
    except Exception as exc:  # noqa: BLE001 - one bad source must not kill the rest
        log.exception("Scrape of %s failed", source.key)
        run.status = ScrapeRun.STATUS_FAILED
        run.error = f"{type(exc).__name__}: {exc}"

    _finish(run, clock, notes)
    return run


def _finish(run: ScrapeRun, clock: float, notes: list[str]) -> None:
    run.finished_at = datetime.now(timezone.utc)
    run.duration_seconds = round(time.monotonic() - clock, 2)
    if notes:
        run.log = {"notes": notes[:100]}


def _university_for(db: Session, source: ScrapeSource) -> CatalogUniversity:
    university = db.execute(
        select(CatalogUniversity).where(CatalogUniversity.slug == source.university_slug)
    ).scalar_one_or_none()
    if university is None:
        university = CatalogUniversity(
            slug=source.university_slug,
            name=source.label,
        )
        db.add(university)
        db.flush()
    return university


def _upsert(
    db: Session,
    university: CatalogUniversity,
    source_key: str,
    records: list[ParsedProfessor],
) -> tuple[int, int]:
    """
    Insert new people, refresh existing ones.

    A record the site no longer lists is left alone rather than deleted — a
    directory redesign that breaks the parser would otherwise wipe the catalog.
    Takedowns (`is_removed`) are never resurrected by a later run.
    """
    now = datetime.now(timezone.utc)
    created = updated = 0

    existing = {
        (p.name.strip().lower(), (p.email or "").strip().lower()): p
        for p in db.execute(
            select(CatalogProfessor).where(CatalogProfessor.university_id == university.id)
        ).scalars()
    }
    by_name = {
        p.name.strip().lower(): p
        for p in existing.values()
        if not p.email
    }

    for record in records:
        key = record.key()
        current = existing.get(key) or (by_name.get(key[0]) if record.email else None)

        if current is None:
            db.add(
                CatalogProfessor(
                    university_id=university.id,
                    name=record.name,
                    title=record.title,
                    department=record.department,
                    research_focus=record.research_focus,
                    email=record.email,
                    profile_url=record.profile_url,
                    source_url=record.source_url,
                    source_key=source_key,
                    scraped_at=now,
                    is_published=True,
                )
            )
            created += 1
            continue

        if current.is_removed:
            # Honour the takedown; just note that the source still lists them.
            current.scraped_at = now
            continue

        changed = False
        for field in ("title", "department", "research_focus", "email", "profile_url"):
            value = getattr(record, field)
            if value and getattr(current, field) != value:
                setattr(current, field, value)
                changed = True
        current.source_url = record.source_url
        current.source_key = source_key
        current.scraped_at = now
        if changed:
            updated += 1

    db.flush()
    return created, updated
