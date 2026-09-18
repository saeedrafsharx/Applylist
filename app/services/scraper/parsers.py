"""
Faculty-directory parsers.

Directory markup varies wildly between universities and changes without notice,
so none of these key on CSS class names. They anchor on structure that survives
redesigns — an email address, a table header, a link whose text is a person's
name — and filter hard, because a page that yields zero rows is a visible
problem while a page that yields garbage rows quietly pollutes the catalog.

`auto` runs every structural strategy and keeps whichever explains the page
best; it is the right choice for a new source unless one strategy is known to
be the only one that fits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser, Node

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")

# Directory pages are full of generic addresses; those aren't people.
GENERIC_LOCALPARTS = {
    "info", "admin", "webmaster", "contact", "office", "enquiries", "inquiries",
    "support", "help", "noreply", "no-reply", "privacy", "media", "press",
    "admissions", "graduate", "grad", "registrar", "hr", "jobs", "recruitment",
    "reception", "dept", "department", "general", "communications", "web",
}

TITLE_WORDS = (
    "professor", "lecturer", "reader", "chair", "fellow", "scientist",
    "instructor", "researcher", "director", "emeritus", "assistant", "associate",
    "investigator", "principal", "dean", "faculty",
)

# Whole words that appear in names of things, never of people. Matched as
# words, not substrings, so "Homer" and "Newsome" survive "home" and "news".
NON_PERSON_WORDS = frozenset({
    "department", "faculty", "school", "university", "institute", "centre",
    "center", "program", "programme", "laboratory", "lab", "office", "research",
    "contact", "home", "about", "people", "directory", "news", "events", "login",
    "search", "menu", "profile", "more", "view", "page", "email", "website",
    "professor", "lecturer", "emeritus", "overview", "publications", "team",
})
NON_PERSON_PHRASES = ("@", "http", "www.", "read more", "click here")

_HONORIFICS = re.compile(
    r"^(?:(?:prof\.?|professor|dr\.?|mr\.?|ms\.?|mrs\.?|miss|mx\.?)\s+)+", re.I
)
_POSTNOMINALS = re.compile(
    r",?\s+(?:ph\.?\s?d\.?|m\.?d\.?|md|phd|msc|m\.sc\.?|mba|frcpc|frcp|facp|p\.?eng\.?)\.?(?=,|\s|$)",
    re.I,
)
_WS = re.compile(r"\s+")

# "name [at] uni [dot] edu", "name (at) uni.edu", "name{at}uni.edu", "name AT uni DOT edu"
_AT = re.compile(r"\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|<\s*at\s*>)\s*", re.I)
_DOT = re.compile(r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|<\s*dot\s*>)\s*", re.I)
# Bare "AT" / "DOT" only in capitals: lower-case "at" is ordinary English.
_AT_CAPS = re.compile(r"(?<=[A-Za-z0-9._%+\-])\s+AT\s+(?=[A-Za-z0-9\-])")
_DOT_CAPS = re.compile(r"(?<=[A-Za-z0-9\-])\s+DOT\s+(?=[A-Za-z0-9\-])")
# The plain-word form ("jane at uni dot edu") only when *both* words appear,
# so prose like "look at this" is never rewritten.
_PLAIN = re.compile(
    r"\b([A-Za-z0-9._%+\-]+)\s+at\s+([A-Za-z0-9\-]+(?:\s+dot\s+[A-Za-z0-9\-]+)+)\b", re.I
)

NEXT_LABELS = {"next", "next page", "next ›", "next »", "›", "»", "next>", "older", ">"}


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

    def as_dict(self) -> dict:
        return {
            "name": self.name, "email": self.email, "title": self.title,
            "department": self.department, "profile_url": self.profile_url,
            "research_focus": (self.research_focus or "")[:160] or None,
        }


# ── text helpers ────────────────────────────────────────────────


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


def deobfuscate(text: str) -> str:
    """Undo the common anti-harvesting spellings so the address can be matched."""
    if not text or not re.search(r"\bat\b|\[|\(|\{|<", text, re.I):
        return text
    text = _AT_CAPS.sub("@", _AT.sub("@", text))
    text = _DOT_CAPS.sub(".", _DOT.sub(".", text))
    text = _PLAIN.sub(_plain_to_email, text)
    return text


def _plain_to_email(match: re.Match) -> str:
    domain = re.sub(r"\s+dot\s+", ".", match.group(2), flags=re.I)
    return f"{match.group(1)}@{domain}"


def emails_in(text: str) -> list[str]:
    """Personal addresses in a piece of text, deobfuscated, in order, deduplicated."""
    seen: dict[str, None] = {}
    for match in EMAIL_RE.finditer(deobfuscate(text)):
        email = match.group(0).strip(".").lower()
        if is_personal_email(email):
            seen.setdefault(email, None)
    return list(seen)


def decode_cfemail(encoded: str) -> Optional[str]:
    """Cloudflare's email protection: hex bytes XOR-ed with the first byte."""
    try:
        data = bytes.fromhex(encoded)
        key = data[0]
        return bytes(b ^ key for b in data[1:]).decode("utf-8")
    except (ValueError, IndexError, UnicodeDecodeError):
        return None


def looks_like_person(name: str) -> bool:
    """Reject nav labels, headings, and department names posing as people."""
    name = clean(name)
    if not (4 <= len(name) <= 80):
        return False
    words = name.split()
    if not (2 <= len(words) <= 6):
        return False
    if any(ch.isdigit() for ch in name):
        return False
    lowered = name.lower()
    if any(phrase in lowered for phrase in NON_PERSON_PHRASES):
        return False
    if NON_PERSON_WORDS.intersection(re.findall(r"[a-z]+", lowered)):
        return False
    # Real names are capitalised; nav items like "our people" are not.
    capitalised = sum(1 for w in words if w[:1].isupper())
    return capitalised >= 2


def normalize_name(name: str) -> str:
    name = _HONORIFICS.sub("", clean(name))
    name = _POSTNOMINALS.sub("", name)
    return clean(name.strip(" ,;|-–—"))


def is_personal_email(email: Optional[str]) -> bool:
    if not email:
        return False
    local = email.split("@", 1)[0].lower()
    if local in GENERIC_LOCALPARTS:
        return False
    # Image names like "logo@2x.png" match the address pattern.
    return not email.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"))


def same_host(url: str, base: str) -> bool:
    def host(u: str) -> str:
        return urlparse(u).netloc.lower().removeprefix("www.")

    return host(url) == host(base)


def _usable_href(href: Optional[str]) -> bool:
    href = (href or "").strip()
    return bool(href) and not href.startswith(("mailto:", "tel:", "#", "javascript:"))


# ── document preparation ────────────────────────────────────────


def prepare(html: str) -> HTMLParser:
    """
    Parse and strip the page chrome, and turn Cloudflare-protected addresses
    back into plain text so every strategy below can see them.
    """
    tree = HTMLParser(html)
    for tag in tree.css("script, style, noscript, template, svg, nav, header, footer, select"):
        tag.decompose()
    for node in tree.css("[data-cfemail]"):
        decoded = decode_cfemail(node.attributes.get("data-cfemail") or "")
        if decoded:
            node.replace_with(decoded)
    for link in tree.css("a[href*='/cdn-cgi/l/email-protection#']"):
        decoded = decode_cfemail((link.attributes.get("href") or "").rsplit("#", 1)[-1])
        if decoded:
            link.attrs["href"] = f"mailto:{decoded}"
    return tree


def content_root(tree: HTMLParser) -> Node:
    for sel in ("main", "[role=main]", "#main-content", "#content", "#main", "article"):
        node = tree.css_first(sel)
        if node is not None and len(node_text(node)) > 200:
            return node
    return tree.body or tree.root


def node_emails(node: Node) -> list[str]:
    """Every personal address inside a node, from mailto links and from its text."""
    found: dict[str, None] = {}
    for link in node.css("a[href^='mailto:'], a[href^='MAILTO:']"):
        href = (link.attributes.get("href") or "")[7:].split("?", 1)[0].strip()
        for email in emails_in(href):
            found.setdefault(email, None)
    for email in emails_in(node_text(node)):
        found.setdefault(email, None)
    return list(found)


# ── field extraction ────────────────────────────────────────────


def extract_email(node: Node, page_text: str = "") -> Optional[str]:
    emails = node_emails(node) or emails_in(page_text)
    return emails[0] if emails else None


def extract_title(block: Optional[Node]) -> Optional[str]:
    """
    The person's academic title, read from whichever element holds it.

    Scanning individual leaf elements rather than the block's flattened text
    keeps the heading out of the result — the name and the title are siblings,
    and a text-level match would return both stuck together.
    """
    if block is None:
        return None
    for node in block.css("*"):
        if node.child is not None and node.child.tag != "-text":
            continue  # only leaf-ish nodes, so we don't re-match a whole card
        candidate = node_text(node)
        if not candidate or len(candidate) > 120 or "@" in candidate:
            continue
        lowered = candidate.lower()
        if any(word in lowered for word in TITLE_WORDS) and not lowered.startswith("research interest"):
            return candidate
    return None


_DEPARTMENT = re.compile(r"^(?:(?:the\s+)?(?:department|dept\.?|school|division|faculty|institute|centre|center)\s+(?:of|for)\s+)", re.I)


def extract_department(block: Node) -> Optional[str]:
    """A "Department of …" style line inside the card, when there is one."""
    for node in block.css("*"):
        if node.child is not None and node.child.tag != "-text":
            continue
        text = node_text(node)
        if 8 <= len(text) <= 120 and _DEPARTMENT.match(text):
            return text
    return None


_FOCUS_LABEL = re.compile(
    r"^(?:research\s+(?:interests?|areas?|focus)|interests?|areas?\s+of\s+research|expertise|keywords?)\s*[:\-–]\s*",
    re.I,
)


def extract_focus(block: Node) -> Optional[str]:
    """An explicitly labelled research-interest line, when the card has one."""
    for node in block.css("*"):
        text = node_text(node)
        if 12 <= len(text) <= 700 and _FOCUS_LABEL.match(text):
            return _FOCUS_LABEL.sub("", text)[:600] or None
    for sel in ("[class*='research']", "[class*='interest']", "[class*='expertise']", "[class*='bio']"):
        node = block.css_first(sel)
        text = node_text(node)
        if len(text) >= 12:
            return _FOCUS_LABEL.sub("", text)[:600]
    return None


def _focus_from(
    block_text: str,
    name: str,
    emails: Iterable[str],
    title: Optional[str] = None,
    labels: Optional[list[str]] = None,
) -> Optional[str]:
    """Whatever prose is left in the card once the chrome is removed."""
    text = deobfuscate(block_text)
    for email in emails:
        text = re.sub(re.escape(email), " ", text, flags=re.I)
    text = text.replace(name, " ")
    if title:
        text = text.replace(title, " ")
    for label in sorted(labels or [], key=len, reverse=True):
        if label and len(label) <= 60:
            text = text.replace(label, " ")
    text = _FOCUS_LABEL.sub("", clean(text))
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    text = re.sub(r"\b(?:email|e-mail|phone|tel|fax|office|website|profile|lab website)\s*:?", " ", text, flags=re.I)
    text = clean(text)
    text = re.sub(r"^[\s|·•\-–—,:;]+", "", text)
    text = re.sub(r"[\s|·•,:;]+$", "", text)
    if len(text) < 12:
        return None
    return text[:600]


def _name_in(block: Node, link: Optional[Node] = None) -> Optional[str]:
    """Find the person's name inside a card."""
    # 1. The mailto link text itself, when it isn't just the address.
    if link is not None:
        text = normalize_name(node_text(link))
        if "@" not in text and looks_like_person(text):
            return text

    # 2. A heading or name-ish element.
    for sel in ("h1", "h2", "h3", "h4", "h5", "h6", "[class*='name']", "strong", "b", "dt"):
        for node in block.css(sel):
            candidate = normalize_name(node_text(node))
            if looks_like_person(candidate):
                return candidate

    # 3. A link whose text is a name (usually to the profile page).
    for anchor in block.css("a"):
        if (anchor.attributes.get("href") or "").lower().startswith("mailto:"):
            continue
        candidate = normalize_name(node_text(anchor))
        if looks_like_person(candidate):
            return candidate

    # 4. A short leaf that reads as a name.
    for node in block.css("*"):
        if node.child is not None and node.child.tag != "-text":
            continue
        candidate = normalize_name(node_text(node))
        if len(candidate) <= 60 and looks_like_person(candidate):
            return candidate

    # No name on the card. Guessing one from the address ("projects.ipn" →
    # "Projects Ipn") invents people, so the card is skipped instead.
    return None


def _profile_link(block: Node, base_url: str, name: str) -> Optional[str]:
    """Prefer a link whose text is the name; otherwise the first same-site link."""
    fallback = None
    for anchor in block.css("a"):
        href = anchor.attributes.get("href")
        if not _usable_href(href):
            continue
        absolute = urljoin(base_url, href.strip())[:500]
        if normalize_name(node_text(anchor)) == name:
            return absolute
        if fallback is None and same_host(absolute, base_url):
            fallback = absolute
    return fallback


# ── strategy 1: tables ──────────────────────────────────────────

_COLUMN_WORDS = {
    "name": ("name", "faculty", "supervisor", "investigator", "p.i", "pi", "member", "person"),
    "email": ("email", "e-mail", "mail", "contact"),
    "website": ("website", "web", "url", "lab", "homepage", "link", "page"),
    "focus": ("research", "interest", "area", "notes", "description", "topic", "expertise", "project", "field"),
    "title": ("title", "position", "rank", "role", "appointment"),
    "department": ("department", "dept", "unit", "program", "affiliation", "school"),
}


def _classify_header(text: str) -> Optional[str]:
    lowered = text.lower().strip()
    if not lowered:
        return None
    # Order matters: "Lab website" is a website, "Research area" is focus.
    for column in ("email", "website", "focus", "title", "department", "name"):
        if any(re.search(rf"\b{re.escape(w)}\b", lowered) for w in _COLUMN_WORDS[column]):
            return column
    return None


def parse_table_directory(html: str, source_url: str) -> list[ParsedProfessor]:
    """
    Directories published as an HTML table, with a header row naming the columns.

    Columns are matched by their header text (Name / E-mail / Website / Notes …),
    not by position, so a reordered table still parses.
    """
    tree = prepare(html)
    found: dict[tuple[str, str], ParsedProfessor] = {}

    for table in tree.css("table"):
        rows = table.css("tr")
        if len(rows) < 2:
            continue
        header_cells = rows[0].css("th, td")
        columns = [_classify_header(node_text(c)) for c in header_cells]
        if "name" not in columns:
            continue

        for row in rows[1:]:
            cells = row.css("td, th")
            if len(cells) < 2:
                continue
            fields: dict[str, Node] = {}
            for index, cell in enumerate(cells[: len(columns)]):
                column = columns[index]
                if column and column not in fields:
                    fields[column] = cell

            name_cell = fields.get("name")
            name = normalize_name(node_text(name_cell)) if name_cell is not None else ""
            if not looks_like_person(name):
                continue

            emails = node_emails(fields["email"]) if "email" in fields else []
            emails = emails or node_emails(row)

            website = None
            for column in ("website", "name"):
                cell = fields.get(column)
                if cell is None:
                    continue
                anchor = next((a for a in cell.css("a") if _usable_href(a.attributes.get("href"))), None)
                if anchor is not None:
                    website = urljoin(source_url, anchor.attributes["href"].strip())[:500]
                    break

            focus = node_text(fields["focus"]) if "focus" in fields else ""
            record = ParsedProfessor(
                name=name,
                source_url=source_url,
                email=emails[0] if emails else None,
                title=(node_text(fields["title"])[:200] or None) if "title" in fields else None,
                department=(node_text(fields["department"])[:200] or None) if "department" in fields else None,
                research_focus=_FOCUS_LABEL.sub("", focus)[:600] if len(focus) >= 12 else None,
                profile_url=website,
            )
            found.setdefault(record.key(), record)

    return list(found.values())


# ── strategy 2: cards anchored on an address ────────────────────


def _card_for(anchor: Node, email: str, root: Node) -> Node:
    """
    Climb from an address to the largest ancestor that still belongs to this
    person alone — i.e. the last one before a second person's address appears.
    That is the card, whatever the site calls it.
    """
    card = anchor
    node = anchor.parent
    while node is not None and node is not root and node.tag not in ("body", "html"):
        others = [e for e in node_emails(node) if e != email]
        if others or len(node_text(node)) > 2500:
            break
        card = node
        node = node.parent
    return card


def parse_mailto_directory(html: str, source_url: str) -> list[ParsedProfessor]:
    """
    Cards or rows that each carry one person's address.

    Anchors on the address — a `mailto:` link, or an address written out in the
    text, including the `name [at] uni [dot] edu` disguises — then reads the
    name, title and blurb from the card around it. Survives redesigns that
    per-class-name selectors do not.
    """
    tree = prepare(html)
    root = content_root(tree)
    found: dict[tuple[str, str], ParsedProfessor] = {}
    seen_emails: set[str] = set()

    anchors: list[tuple[Node, str, Optional[Node]]] = []
    for link in root.css("a[href^='mailto:'], a[href^='MAILTO:']"):
        for email in emails_in((link.attributes.get("href") or "")[7:].split("?", 1)[0]):
            anchors.append((link, email, link))
    # Addresses written as text: anchor on the smallest element that contains them.
    for node in root.css("*"):
        if node.tag in ("a", "html", "body"):
            continue
        own_text = clean(" ".join(
            (child.text_content or "") for child in node.iter(include_text=True) if child.tag == "-text"
        ))
        for email in emails_in(own_text):
            anchors.append((node, email, None))

    for anchor, email, link in anchors:
        if email in seen_emails:
            continue
        card = _card_for(anchor, email, root)
        name = _name_in(card, link)
        if not name:
            continue
        seen_emails.add(email)

        title = extract_title(card)
        labels = [node_text(a) for a in card.css("a")]
        focus = extract_focus(card) or _focus_from(node_text(card), name, [email], title, labels)
        record = ParsedProfessor(
            name=name,
            source_url=source_url,
            email=email,
            title=title[:200] if title else None,
            department=extract_department(card),
            research_focus=focus,
            profile_url=_profile_link(card, source_url, name),
        )
        found.setdefault(record.key(), record)

    return list(found.values())


# ── strategy 3: names that link to profile pages ────────────────


def parse_profile_links(html: str, source_url: str) -> list[ParsedProfessor]:
    """
    Index pages that list names linking to individual profile pages and publish
    no addresses. Produces records with a `profile_url` and no email; the runner
    follows those links to fill the rest in.
    """
    tree = prepare(html)
    root = content_root(tree)
    found: dict[tuple[str, str], ParsedProfessor] = {}
    for anchor in root.css("a"):
        href = anchor.attributes.get("href")
        if not _usable_href(href):
            continue
        name = normalize_name(node_text(anchor))
        if not looks_like_person(name):
            continue
        absolute = urljoin(source_url, href.strip())
        if not same_host(absolute, source_url):
            continue
        record = ParsedProfessor(name=name, source_url=source_url, profile_url=absolute[:500])
        found.setdefault(record.key(), record)
    return list(found.values())


# ── auto ────────────────────────────────────────────────────────


def _score(records: list[ParsedProfessor]) -> float:
    with_email = sum(1 for r in records if r.email)
    with_focus = sum(1 for r in records if r.research_focus)
    return with_email * 3 + with_focus + len(records) * 0.5


def parse_auto(html: str, source_url: str) -> list[ParsedProfessor]:
    """
    Try every structural strategy and keep the one that explains the page best:
    most people with addresses, then most with research descriptions.

    The index-of-names strategy is the fallback, used only when no strategy
    finds a single address — its output is names and links, which the runner
    then enriches from the profile pages.
    """
    table = parse_table_directory(html, source_url)
    cards = parse_mailto_directory(html, source_url)
    # A table with named columns is the more reliable reading of the same page,
    # so it wins unless the card strategy finds clearly more.
    best = table if table and _score(table) * 1.25 >= _score(cards) else cards
    if any(r.email for r in best):
        return best
    return parse_profile_links(html, source_url) or best


# ── profile pages and pagination ────────────────────────────────


def enrich_from_profile(record: ParsedProfessor, html: str) -> ParsedProfessor:
    """Fill in email / title / focus by reading a person's own profile page."""
    tree = prepare(html)
    body = content_root(tree)
    if body is None:
        return record

    if not record.email:
        emails = node_emails(body)
        # A profile page with many addresses is a list, not this person's page.
        if 1 <= len(emails) <= 3:
            record.email = emails[0]
    if not record.title:
        title = extract_title(body)
        record.title = title[:200] if title else None
    if not record.research_focus:
        record.research_focus = extract_focus(body)
    if not record.research_focus:
        for node in body.css("p"):
            candidate = node_text(node)
            if 60 <= len(candidate) <= 900:
                record.research_focus = candidate[:600]
                break
    return record


def find_next_page(html: str, current_url: str) -> Optional[str]:
    """The URL of the directory's next page, if it is paginated."""
    tree = HTMLParser(html)
    candidates: list[str] = []
    for link in tree.css("link[rel=next], a[rel=next], a[rel='next']"):
        candidates.append(link.attributes.get("href") or "")
    for anchor in tree.css(".pager__item--next a, .pagination .next a, a.next, li.next a, a[aria-label^='Next'], a[title^='Next'], a[title^='next']"):
        candidates.append(anchor.attributes.get("href") or "")
    for anchor in tree.css("a"):
        if node_text(anchor).lower() in NEXT_LABELS:
            candidates.append(anchor.attributes.get("href") or "")

    for href in candidates:
        if not _usable_href(href):
            continue
        absolute = urljoin(current_url, href.strip())
        if same_host(absolute, current_url) and absolute.rstrip("/") != current_url.rstrip("/"):
            return absolute
    return None


PARSERS: dict[str, Callable[[str, str], list[ParsedProfessor]]] = {
    "auto": parse_auto,
    "mailto_directory": parse_mailto_directory,
    "table_directory": parse_table_directory,
    "profile_links": parse_profile_links,
}

PARSER_HELP = {
    "auto": "Tries every strategy and keeps the best. Use this unless you know better.",
    "mailto_directory": "Cards or rows that each show one person's email (plain, mailto, or disguised as [at]).",
    "table_directory": "An HTML table with a header row (Name, Email, Website, Research …).",
    "profile_links": "A list of names linking to profile pages; each profile is visited for the details.",
}
