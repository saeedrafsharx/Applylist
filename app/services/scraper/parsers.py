from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser, Node

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Directory pages are full of generic addresses; those aren't people.
GENERIC_LOCALPARTS = {
    "info", "admin", "webmaster", "contact", "office", "enquiries", "inquiries",
    "support", "help", "noreply", "no-reply", "privacy", "media", "press",
    "admissions", "graduate", "grad", "registrar", "hr", "jobs", "recruitment",
}

TITLE_WORDS = (
    "professor", "lecturer", "reader", "chair", "fellow", "scientist",
    "instructor", "researcher", "director", "emeritus", "assistant", "associate",
)

_HONORIFICS = re.compile(r"^(prof\.?|professor|dr\.?|mr\.?|ms\.?|mrs\.?|miss)\s+", re.I)
_WS = re.compile(r"\s+")


@dataclass(slots=True)
class ParsedProfessor:
    name: str
    source_url: str
    title: Optional[str] = None
    department: Optional[str] = None
    research_focus: Optional[str] = None
    email: Optional[str] = None
    profile_url: Optional[str] = None

    def key(self) -> tuple[str, str]:
        return (self.name.strip().lower(), (self.email or "").strip().lower())


# ── helpers ─────────────────────────────────────────────────────


def clean(text: Optional[str]) -> str:
    return _WS.sub(" ", (text or "")).strip()


def node_text(node: Optional[Node]) -> str:
    """
    Flattened text of a node.

    selectolax joins child nodes with no separator by default, which silently
    welds adjacent elements together ("Danilo Bzdok" + "Associate Professor"
    becoming one word). Always read text through here.
    """
    if node is None:
        return ""
    return clean(node.text(separator=" ", strip=True))


def looks_like_person(name: str) -> bool:
    """Reject nav labels, headings, and department names posing as people."""
    name = clean(name)
    if not (4 <= len(name) <= 80):
        return False
    words = name.split()
    if not (2 <= len(words) <= 5):
        return False
    if any(ch.isdigit() for ch in name):
        return False
    lowered = name.lower()
    if any(bad in lowered for bad in ("department", "faculty of", "school of", "university", "@")):
        return False
    # Real names are capitalised; nav items like "our people" are not.
    capitalised = sum(1 for w in words if w[:1].isupper())
    return capitalised >= 2


def normalize_name(name: str) -> str:
    return clean(_HONORIFICS.sub("", clean(name)))


def is_personal_email(email: Optional[str]) -> bool:
    if not email:
        return False
    local = email.split("@", 1)[0].lower()
    return local not in GENERIC_LOCALPARTS


def extract_email(node: Node, page_text: str = "") -> Optional[str]:
    for link in node.css("a[href^='mailto:']"):
        href = link.attributes.get("href") or ""
        candidate = href[7:].split("?", 1)[0].strip()
        if EMAIL_RE.fullmatch(candidate) and is_personal_email(candidate):
            return candidate.lower()
    match = EMAIL_RE.search(node_text(node) or page_text)
    if match and is_personal_email(match.group(0)):
        return match.group(0).lower()
    return None


def extract_title(block: Optional[Node]) -> Optional[str]:
    """
    The person's academic title, read from whichever child element holds it.

    Scanning individual elements rather than the block's flattened text keeps
    the heading out of the result - the name and the title are siblings, and a
    text-level match would return both stuck together.
    """
    if block is None:
        return None
    for node in block.css("*"):
        if node.child is not None and node.child.tag != "-text":
            continue  # only leaf-ish nodes, so we don't re-match a whole card
        candidate = node_text(node)
        if not candidate or len(candidate) > 120:
            continue
        lowered = candidate.lower()
        if any(word in lowered for word in TITLE_WORDS):
            return candidate
    return None


def same_host(url: str, base: str) -> bool:
    return urlparse(url).netloc == urlparse(base).netloc


# ── parsers ─────────────────────────────────────────────────────


def parse_mailto_directory(html: str, source_url: str) -> list[ParsedProfessor]:
    """
    Generic faculty-directory parser, anchored on `mailto:` links.

    Directory markup varies wildly between universities, but a listing that is
    useful to us almost always publishes the address as a mailto link. We walk
    up from each one to the enclosing row/card and read the name and blurb out
    of that block. This degrades gracefully when a site redesigns, which
    per-class-name selectors do not.
    """
    tree = HTMLParser(html)
    for tag in tree.css("script, style, nav, header, footer"):
        tag.decompose()

    found: dict[tuple[str, str], ParsedProfessor] = {}

    for link in tree.css("a[href^='mailto:']"):
        href = link.attributes.get("href") or ""
        email = href[7:].split("?", 1)[0].strip().lower()
        if not EMAIL_RE.fullmatch(email) or not is_personal_email(email):
            continue

        block = link
        for _ in range(5):
            if block.parent is None:
                break
            block = block.parent
            text = node_text(block)
            if len(text) > 40:
                break

        name = _name_near(link, block, email)
        if not name:
            continue

        block_text = node_text(block)
        title = extract_title(block)
        # "Email", "Profile", "Website"... are link labels, not research interests.
        link_labels = [node_text(a) for a in block.css("a")]

        record = ParsedProfessor(
            name=name,
            source_url=source_url,
            email=email,
            title=title,
            research_focus=_focus_from(block_text, name, email, title, link_labels),
            profile_url=_profile_link_near(block, source_url, email),
        )
        found.setdefault(record.key(), record)

    return list(found.values())


def _name_near(link: Node, block: Node, email: str) -> Optional[str]:
    """Find the person's name around a mailto link."""
    # 1. The link text itself, when it isn't just the address.
    link_text = normalize_name(node_text(link))
    if link_text and "@" not in link_text and looks_like_person(link_text):
        return link_text

    # 2. A heading inside the block.
    for sel in ("h1", "h2", "h3", "h4", "h5", "strong", "b", ".name", "[class*='name']"):
        for node in block.css(sel):
            candidate = normalize_name(node_text(node))
            if looks_like_person(candidate):
                return candidate

    # 3. Another anchor in the block that links to a profile page.
    for anchor in block.css("a"):
        href = anchor.attributes.get("href") or ""
        if href.startswith("mailto:"):
            continue
        candidate = normalize_name(node_text(anchor))
        if looks_like_person(candidate):
            return candidate

    # 4. The local part of the address, as a last resort (flast / first.last).
    local = email.split("@", 1)[0]
    if "." in local:
        guess = " ".join(part.capitalize() for part in local.split(".") if part.isalpha())
        if looks_like_person(guess):
            return guess
    return None


def _profile_link_near(block: Node, source_url: str, email: str) -> Optional[str]:
    for anchor in block.css("a"):
        href = (anchor.attributes.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        absolute = urljoin(source_url, href)
        if same_host(absolute, source_url):
            return absolute[:500]
    return None


def _focus_from(
    block_text: str,
    name: str,
    email: str,
    title: Optional[str] = None,
    link_labels: Optional[list[str]] = None,
) -> Optional[str]:
    """Whatever prose is left in the card once the chrome is removed."""
    text = block_text.replace(email, " ").replace(name, " ")
    if title:
        text = text.replace(title, " ")
    for label in sorted(link_labels or [], key=len, reverse=True):
        if label and len(label) <= 40:
            text = text.replace(label, " ")
    text = clean(text)
    text = re.sub(r"^[\s|·•\-–—,:]+", "", text)
    text = re.sub(r"[\s|·•,:]+$", "", text)
    if len(text) < 12:
        return None
    return text[:600]


def parse_profile_links(html: str, source_url: str) -> list[ParsedProfessor]:
    """
    For index pages that list names linking to individual profile pages and
    publish no addresses. Produces records with a `profile_url` and no email;
    the runner can follow those links when the source enables it.
    """
    tree = HTMLParser(html)
    for tag in tree.css("script, style, nav, header, footer"):
        tag.decompose()

    found: dict[tuple[str, str], ParsedProfessor] = {}
    for anchor in tree.css("a"):
        href = (anchor.attributes.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        name = normalize_name(node_text(anchor))
        if not looks_like_person(name):
            continue
        absolute = urljoin(source_url, href)
        if not same_host(absolute, source_url):
            continue
        record = ParsedProfessor(
            name=name, source_url=source_url, profile_url=absolute[:500]
        )
        found.setdefault(record.key(), record)
    return list(found.values())


def enrich_from_profile(record: ParsedProfessor, html: str) -> ParsedProfessor:
    """Fill in email / title / focus by reading a person's own profile page."""
    tree = HTMLParser(html)
    for tag in tree.css("script, style, nav, header, footer"):
        tag.decompose()
    body = tree.body or tree.root
    if body is None:
        return record

    if not record.email:
        record.email = extract_email(body)
    text = node_text(body)
    if not record.title:
        record.title = extract_title(body)
    if not record.research_focus:
        for sel in ("[class*='research']", "[id*='research']", "p"):
            for node in body.css(sel):
                candidate = node_text(node)
                if 40 <= len(candidate) <= 600:
                    record.research_focus = candidate
                    break
            if record.research_focus:
                break
        if not record.research_focus and len(text) > 40:
            record.research_focus = text[:600]
    return record


PARSERS: dict[str, Callable[[str, str], list[ParsedProfessor]]] = {
    "mailto_directory": parse_mailto_directory,
    "profile_links": parse_profile_links,
}
