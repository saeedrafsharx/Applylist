"""
Language selection and translation coverage. No database or network needed.
"""

from __future__ import annotations

import glob
import re
from datetime import date

import pytest
from starlette.requests import Request

from app import i18n
from app.i18n.fa import FA

_CALL = re.compile(r'''\b_\(\s*(?P<q>["'])(?P<s>(?:\\.|(?!(?P=q)).)*?)(?P=q)''', re.S)
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _request(headers=None, cookies=None, client=("203.0.113.9", 1234)) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookies:
        raw.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    return Request({"type": "http", "headers": raw, "client": client, "path": "/"})


def _template_strings() -> set[str]:
    found = set()
    for path in glob.glob("templates/**/*.html", recursive=True):
        if "/admin/" in path:
            continue  # the admin panel is English-only by design
        for match in _CALL.finditer(open(path, encoding="utf-8").read()):
            found.add(match.group("s").replace('\\"', '"').replace("\\'", "'"))
    return found


def test_every_template_string_has_a_farsi_translation():
    missing = sorted(s for s in _template_strings() if s not in FA)
    assert not missing, "Add these to app/i18n/fa.py:\n" + "\n".join(missing)


@pytest.mark.parametrize("key", sorted(FA))
def test_translations_keep_their_placeholders(key):
    assert set(_PLACEHOLDER.findall(key)) == set(_PLACEHOLDER.findall(FA[key])), key


def test_translate_falls_back_to_english_and_fills_placeholders():
    i18n.set_lang("fa")
    try:
        assert i18n._("Professors") == FA["Professors"]
        assert i18n._("A string nobody translated") == "A string nobody translated"
        assert "Jane" in i18n._("Added {name}.", name="Jane")
    finally:
        i18n.set_lang("en")
    assert i18n._("Added {name}.", name="Jane") == "Added Jane."


def test_explicit_choice_beats_location():
    request = _request(headers={"x-forwarded-for": "2.176.0.1"}, cookies={"lang": "en"})
    assert i18n.resolve_language(request) == "en"


def test_iranian_ip_gets_farsi():
    assert i18n.is_iranian_ip("2.176.0.1")          # an Iranian allocation
    assert not i18n.is_iranian_ip("8.8.8.8")
    assert not i18n.is_iranian_ip("not-an-ip")
    assert i18n.resolve_language(_request(headers={"x-forwarded-for": "2.176.0.1, 10.0.0.1"})) == "fa"


def test_country_header_wins_over_ip_ranges():
    request = _request(headers={"cf-ipcountry": "DE", "x-forwarded-for": "2.176.0.1"})
    assert i18n.resolve_language(request) == "en"
    assert i18n.resolve_language(_request(headers={"ar-real-country": "IR"})) == "fa"


def test_browser_language_decides_outside_iran():
    assert i18n.resolve_language(_request(headers={"accept-language": "fa-IR,fa;q=0.9"})) == "fa"
    # Farsi as a second preference doesn't switch an English speaker.
    assert i18n.resolve_language(_request(headers={"accept-language": "en-US,fa;q=0.5"})) == "en"
    assert i18n.resolve_language(_request()) == "en"


def test_dates_render_on_the_jalali_calendar_in_farsi():
    from app.deps import _fmt_date

    i18n.set_lang("fa")
    try:
        assert _fmt_date(date(2026, 9, 18)) == "1405-06-27"
    finally:
        i18n.set_lang("en")
    assert _fmt_date(date(2026, 9, 18)) == "2026-09-18"
