from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import session_scope
from ...models import CatalogProfessor, CatalogUniversity, ScrapeRun, ScrapeSource
from .fetcher import FetchError, PoliteFetcher, RobotsDisallowed
from .parsers import PARSERS, ParsedProfessor, enrich_from_profile, find_next_page

log = logging.getLogger(__name__)

# How many index pages one run will page through, and how many individual
# profile pages it may follow on top of them. Both sit under the fetcher's own
# overall page budget (SCRAPER_MAX_PAGES).
MAX_INDEX_PAGES = 30
MAX_PROFILE_FOLLOWS = 40

# How many parsed records a run keeps in its log for the admin to eyeball.
SAMPLE_SIZE = 25

# A run still "running" after this long died with its worker (a restart or a
# deploy mid-crawl); it is reported as interrupted rather than left spinning.
STALE_AFTER = timedelta(hours=2)


def run_source(
    db: Session,
    source: ScrapeSource,
    *,
    triggered_by_user_id: Optional[int] = None,
    follow_profiles: bool = True,
    dry_run: bool = False,
    commit_progress: bool = False,
) -> ScrapeRun:
    """
    Fetch one configured directory — every page of it — and upsert what it lists.

    Every run is recorded — including the ones that fetch nothing — so a parser
    that silently stops matching shows up in the admin list as a run with zero
    records rather than as nothing at all.

    `commit_progress` commits the run row as it goes, so a crawl running in the
    background is visible (and its page count ticks up) while it works.
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
    if commit_progress:
        db.commit()

    notes: list[str] = []
    records: dict[tuple[str, str], ParsedProfessor] = {}

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

            def progress() -> None:
                run.pages_fetched = fetcher.pages_fetched
                if commit_progress:
                    db.commit()

            records = collect(
                fetcher, source.start_url, parser,
                follow_profiles=follow_profiles, notes=notes, on_progress=progress,
            )
            run.pages_fetched = fetcher.pages_fetched

        usable = [r for r in records.values() if r.email or r.profile_url]
        run.records_found = len(usable)
        missing = len(records) - len(usable)
        if missing:
            notes.append(f"dropped {missing} names with neither an address nor a profile link")

        if not dry_run:
            created, updated = _upsert(db, university, source.key, usable)
            run.records_created = created
            run.records_updated = updated
        else:
            notes.append("dry run — nothing written")

        run.log = {"sample": [r.as_dict() for r in usable[:SAMPLE_SIZE]]}
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


def collect(
    fetcher: PoliteFetcher,
    start_url: str,
    parser: Callable[[str, str], list[ParsedProfessor]],
    *,
    follow_profiles: bool = True,
    notes: Optional[list[str]] = None,
    on_progress: Optional[Callable[[], None]] = None,
) -> dict[tuple[str, str], ParsedProfessor]:
    """
    Page through a directory and return everyone it lists, keyed by (name, email).

    Pure crawling: no database. Profile pages are visited only for people the
    index lists without an address.
    """
    notes = notes if notes is not None else []
    records: dict[tuple[str, str], ParsedProfessor] = {}

    url: Optional[str] = start_url
    seen_pages: set[str] = set()
    for page_no in range(1, MAX_INDEX_PAGES + 1):
        if url is None or url in seen_pages:
            break
        seen_pages.add(url)
        try:
            html = fetcher.get(url)
        except FetchError as exc:
            if page_no == 1:
                raise
            notes.append(f"page {page_no}: stopped ({exc})")
            break

        page_url = fetcher.last_url or url
        seen_pages.add(page_url)
        if page_no == 1 and page_url.rstrip("/") != url.rstrip("/"):
            notes.append(f"redirected to {page_url}")
        page_records = parser(html, page_url)
        new = 0
        for record in page_records:
            record.source_url = start_url
            if record.key() not in records:
                records[record.key()] = record
                new += 1
        notes.append(f"page {page_no}: {len(page_records)} records ({new} new) at {page_url}")
        if on_progress:
            on_progress()

        # A "next" page that adds nobody is a pager loop, not more people.
        if page_no > 1 and new == 0:
            break
        url = find_next_page(html, page_url)

    if follow_profiles:
        needs_detail = [r for r in records.values() if not r.email and r.profile_url]
        if needs_detail:
            notes.append(
                f"following {min(len(needs_detail), MAX_PROFILE_FOLLOWS)} profile pages for missing addresses"
            )
        for record in needs_detail[:MAX_PROFILE_FOLLOWS]:
            try:
                enrich_from_profile(record, fetcher.get(record.profile_url))  # type: ignore[arg-type]
            except RobotsDisallowed:
                notes.append(f"robots: skipped profile {record.profile_url}")
            except FetchError as exc:
                notes.append(f"skip {record.profile_url}: {exc}")
                if "Page budget" in str(exc):
                    break
            if on_progress:
                on_progress()

    return records


def run_in_background(source_ids: list[int], triggered_by_user_id: Optional[int], dry_run: bool) -> None:
    """
    Entry point for a crawl started from the admin panel.

    Runs after the response has been sent (FastAPI BackgroundTasks), so a crawl
    that takes minutes — Crawl-delay: 10 is common — can't hit a proxy timeout.
    Each source gets its own session: one failing source rolls back only itself.
    """
    for source_id in source_ids:
        try:
            with session_scope() as db:
                source = db.get(ScrapeSource, source_id)
                if source is None:
                    continue
                run = run_source(
                    db, source,
                    triggered_by_user_id=triggered_by_user_id,
                    dry_run=dry_run,
                    commit_progress=True,
                )
                log.info("Scrape of %s finished: %s, %s found", source.key, run.status, run.records_found)
        except Exception:  # noqa: BLE001 - keep going with the next source
            log.exception("Background scrape of source %s crashed", source_id)


def is_running(db: Session, source_key: str) -> bool:
    cutoff = datetime.now(timezone.utc) - STALE_AFTER
    return db.execute(
        select(ScrapeRun.id).where(
            ScrapeRun.source_key == source_key,
            ScrapeRun.status == ScrapeRun.STATUS_RUNNING,
            ScrapeRun.started_at > cutoff,
        ).limit(1)
    ).first() is not None


def expire_stale_runs(db: Session) -> int:
    """Mark runs orphaned by a restart as failed, so they stop showing as running."""
    cutoff = datetime.now(timezone.utc) - STALE_AFTER
    stale = list(db.execute(
        select(ScrapeRun).where(
            ScrapeRun.status == ScrapeRun.STATUS_RUNNING,
            ScrapeRun.started_at < cutoff,
        )
    ).scalars())
    for run in stale:
        run.status = ScrapeRun.STATUS_FAILED
        run.error = "Interrupted — the server restarted or the worker died mid-run."
        run.finished_at = run.finished_at or datetime.now(timezone.utc)
    return len(stale)


def _finish(run: ScrapeRun, clock: float, notes: list[str]) -> None:
    run.finished_at = datetime.now(timezone.utc)
    run.duration_seconds = round(time.monotonic() - clock, 2)
    log_data = dict(run.log or {})
    if notes:
        log_data["notes"] = notes[:200]
    run.log = log_data or None


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

    rows = list(
        db.execute(
            select(CatalogProfessor).where(CatalogProfessor.university_id == university.id)
        ).scalars()
    )
    existing = {(p.name.strip().lower(), (p.email or "").strip().lower()): p for p in rows}
    by_email = {p.email.strip().lower(): p for p in rows if p.email}
    by_name = {p.name.strip().lower(): p for p in rows if not p.email}

    for record in records:
        key = record.key()
        current = (
            existing.get(key)
            # Same address under a reformatted name ("Jane Doe" → "Jane A. Doe").
            or (by_email.get(key[1]) if key[1] else None)
            # A name-only row that has now gained an address.
            or (by_name.get(key[0]) if record.email else None)
        )

        if current is None:
            row = CatalogProfessor(
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
            db.add(row)
            existing[key] = row
            if record.email:
                by_email[key[1]] = row
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
