from __future__ import annotations

import logging
import time
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

from ...config import settings

log = logging.getLogger(__name__)


class RobotsDisallowed(RuntimeError):
    """The site's robots.txt forbids fetching this URL with our user agent."""


class FetchError(RuntimeError):
    pass


@dataclass
class PoliteFetcher:
    """
    An HTTP client that behaves itself on someone else's server.

    - Reads and obeys robots.txt for our own user agent (per host, cached).
    - Honors Crawl-delay when the site sets one, otherwise uses our configured
      delay, measured per host so one slow site doesn't stall another.
    - Identifies itself with a contactable User-Agent.
    - Caps total pages per run.
    """

    user_agent: str = field(default_factory=lambda: settings.scraper_user_agent)
    delay: float = field(default_factory=lambda: settings.scraper_delay_seconds)
    timeout: float = field(default_factory=lambda: settings.scraper_timeout_seconds)
    max_pages: int = field(default_factory=lambda: settings.scraper_max_pages)
    respect_robots: bool = field(default_factory=lambda: settings.scraper_respect_robots)

    pages_fetched: int = 0
    _robots: dict[str, Optional[robotparser.RobotFileParser]] = field(default_factory=dict)
    _last_hit: dict[str, float] = field(default_factory=dict)
    _client: Optional[httpx.Client] = None

    def __enter__(self) -> "PoliteFetcher":
        self._client = httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en",
            },
        )
        return self

    def __exit__(self, *exc_info) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ── robots ──────────────────────────────────────────────────

    def _robots_for(self, url: str) -> Optional[robotparser.RobotFileParser]:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]

        parser = robotparser.RobotFileParser()
        robots_url = urljoin(origin, "/robots.txt")
        try:
            assert self._client is not None
            resp = self._client.get(robots_url)
            if resp.status_code >= 400:
                # No robots.txt published - RFC 9309 says that means allowed.
                parser = None  # type: ignore[assignment]
            else:
                parser.parse(resp.text.splitlines())
        except httpx.HTTPError as exc:
            log.warning("Could not read %s (%s); treating as allow-all", robots_url, exc)
            parser = None  # type: ignore[assignment]

        self._robots[origin] = parser
        return parser

    def can_fetch(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parser = self._robots_for(url)
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    def _crawl_delay(self, url: str) -> float:
        if not self.respect_robots:
            return self.delay
        parser = self._robots_for(url)
        if parser is None:
            return self.delay
        try:
            declared = parser.crawl_delay(self.user_agent)
        except Exception:  # noqa: BLE001 - malformed robots shouldn't crash a run
            declared = None
        return max(self.delay, float(declared)) if declared else self.delay

    # ── fetching ────────────────────────────────────────────────

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        delay = self._crawl_delay(url)
        last = self._last_hit.get(host)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < delay:
                time.sleep(delay - elapsed)
        self._last_hit[host] = time.monotonic()

    def get(self, url: str) -> str:
        if self._client is None:
            raise FetchError("PoliteFetcher must be used as a context manager.")
        if self.pages_fetched >= self.max_pages:
            raise FetchError(f"Page budget reached ({self.max_pages}).")
        if not self.can_fetch(url):
            raise RobotsDisallowed(f"robots.txt disallows {url} for {self.user_agent}")

        self._wait(url)
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as exc:
            raise FetchError(f"Could not fetch {url}: {exc}") from exc

        self.pages_fetched += 1
        if resp.status_code >= 400:
            raise FetchError(f"{url} returned HTTP {resp.status_code}")
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "xml" not in ctype:
            raise FetchError(f"{url} returned {ctype or 'unknown content type'}, expected HTML")
        return resp.text
