"""
Two-language UI: English and Farsi.

Strings are written in English in templates and code and wrapped in `_()`;
`fa.py` maps each English string to its Farsi translation. A string with no
translation falls back to English, so a missing entry degrades to readable
text rather than breaking a page.

The language for a request is decided once, in `LanguageMiddleware`, and kept
in a context variable so `_()` works anywhere below it — templates, routers,
flash messages — without threading a `lang` argument through every call.
"""

from __future__ import annotations

import bisect
import ipaddress
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Optional

from starlette.requests import Request

from .fa import FA

LANGUAGES = {"en": "English", "fa": "فارسی"}
RTL = {"fa"}
DEFAULT = "en"
COOKIE = "lang"

_current: ContextVar[str] = ContextVar("lang", default=DEFAULT)

# Country headers set by CDNs and reverse proxies, checked in order. Whichever
# the host happens to add is trusted; with none, the IP ranges below decide.
_COUNTRY_HEADERS = ("cf-ipcountry", "ar-real-country", "x-country-code", "x-geo-country", "x-country")


def get_lang() -> str:
    return _current.get()


def set_lang(lang: str) -> None:
    _current.set(lang if lang in LANGUAGES else DEFAULT)


def is_rtl(lang: Optional[str] = None) -> bool:
    return (lang or get_lang()) in RTL


def _(text: str, /, **params) -> str:
    """Translate `text` into the current language, then fill `{placeholders}`."""
    if get_lang() == "fa":
        text = FA.get(text, text)
    return text.format(**params) if params else text


# ── choosing a language ─────────────────────────────────────────


def resolve_language(request: Request) -> str:
    """
    Explicit choice first, then location, then the browser's preference.

    1. The `lang` cookie, set by the language switcher.
    2. A country header from the proxy/CDN, when one is present.
    3. Whether the client IP belongs to an Iranian network.
    4. `Accept-Language`, for Farsi speakers outside Iran.
    """
    chosen = request.cookies.get(COOKIE)
    if chosen in LANGUAGES:
        return chosen

    for header in _COUNTRY_HEADERS:
        country = (request.headers.get(header) or "").strip().upper()
        if len(country) == 2:
            return "fa" if country == "IR" else _from_accept_language(request)

    if is_iranian_ip(_client_ip(request)):
        return "fa"
    return _from_accept_language(request)


def _from_accept_language(request: Request) -> str:
    header = (request.headers.get("accept-language") or "").lower()
    # Only the first preference: "en-US,fa;q=0.5" is an English speaker.
    first = header.split(",", 1)[0].strip()
    return "fa" if first.startswith("fa") else DEFAULT


def _client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real = request.headers.get("x-real-ip") or request.headers.get("ar-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else None


# ── Iranian networks ────────────────────────────────────────────


@lru_cache(maxsize=1)
def _networks() -> dict[int, tuple[list[int], list[int]]]:
    """Sorted (start, end) integer ranges per IP version, loaded once."""
    ranges: dict[int, list[tuple[int, int]]] = {4: [], 6: []}
    path = Path(__file__).with_name("ir_networks.txt")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        net = ipaddress.ip_network(line, strict=False)
        ranges[net.version].append((int(net.network_address), int(net.broadcast_address)))
    out = {}
    for version, items in ranges.items():
        items.sort()
        out[version] = ([start for start, _end in items], [end for _start, end in items])
    return out


def is_iranian_ip(ip: Optional[str]) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    starts, ends = _networks()[addr.version]
    value = int(addr)
    i = bisect.bisect_right(starts, value) - 1
    return i >= 0 and value <= ends[i]
