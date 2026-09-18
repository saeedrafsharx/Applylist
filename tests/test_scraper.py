"""
Parser and politeness tests. These run entirely on fixture HTML - no network.
"""

from __future__ import annotations

from app.services.scraper.parsers import (
    ParsedProfessor,
    is_personal_email,
    looks_like_person,
    normalize_name,
    parse_mailto_directory,
    parse_profile_links,
)

DIRECTORY_HTML = """
<html><body>
  <nav><a href="mailto:webmaster@uni.test">webmaster@uni.test</a></nav>
  <div class="faculty-list">
    <div class="card">
      <h3>Danilo Bzdok</h3>
      <p class="title">Associate Professor</p>
      <p>Computational neuroimaging and machine learning applied to brain data.</p>
      <a href="mailto:danilo.bzdok@uni.test">Email</a>
      <a href="/people/bzdok">Profile</a>
    </div>
    <div class="card">
      <h3>Prof. Boris Bernhardt</h3>
      <p>Network analysis, neuroimaging, connectome gradients.</p>
      <a href="mailto:boris.bernhardt@uni.test">boris.bernhardt@uni.test</a>
    </div>
    <div class="card">
      <h3>Graduate Admissions Office</h3>
      <a href="mailto:admissions@uni.test">admissions@uni.test</a>
    </div>
  </div>
</body></html>
"""

INDEX_HTML = """
<html><body>
  <nav><a href="/home">Home</a><a href="/contact">Contact us</a></nav>
  <ul>
    <li><a href="/profiles/gunnar-blohm">Gunnar Blohm</a></li>
    <li><a href="/profiles/jason-gallivan">Jason Gallivan</a></li>
    <li><a href="https://elsewhere.test/someone">External Person</a></li>
    <li><a href="/department-of-psychology">Department of Psychology</a></li>
  </ul>
</body></html>
"""


def test_mailto_parser_finds_people_with_their_details():
    rows = parse_mailto_directory(DIRECTORY_HTML, "https://uni.test/faculty")
    by_email = {r.email: r for r in rows}

    assert "danilo.bzdok@uni.test" in by_email
    bzdok = by_email["danilo.bzdok@uni.test"]
    assert bzdok.name == "Danilo Bzdok"
    assert "neuroimaging" in (bzdok.research_focus or "").lower()
    assert bzdok.profile_url == "https://uni.test/people/bzdok"
    assert bzdok.source_url == "https://uni.test/faculty"


def test_mailto_parser_strips_honorifics_from_names():
    rows = parse_mailto_directory(DIRECTORY_HTML, "https://uni.test/faculty")
    names = {r.name for r in rows}
    assert "Boris Bernhardt" in names
    assert "Prof. Boris Bernhardt" not in names


def test_mailto_parser_skips_generic_department_addresses():
    """An admissions or webmaster inbox is not a person and must not be listed."""
    rows = parse_mailto_directory(DIRECTORY_HTML, "https://uni.test/faculty")
    emails = {r.email for r in rows}
    assert "admissions@uni.test" not in emails
    assert "webmaster@uni.test" not in emails


def test_profile_link_parser_collects_names_and_skips_navigation():
    rows = parse_profile_links(INDEX_HTML, "https://uni.test/faculty")
    names = {r.name for r in rows}
    assert "Gunnar Blohm" in names
    assert "Jason Gallivan" in names
    assert "Department of Psychology" not in names
    assert "Contact us" not in names


def test_profile_link_parser_stays_on_the_same_host():
    rows = parse_profile_links(INDEX_HTML, "https://uni.test/faculty")
    assert all(r.profile_url.startswith("https://uni.test") for r in rows)
    assert "External Person" not in {r.name for r in rows}


def test_parser_returns_nothing_for_an_unrecognised_page():
    """A redesigned page should yield zero rows, not garbage rows."""
    rows = parse_mailto_directory("<html><body><p>Nothing here</p></body></html>", "https://x.test")
    assert rows == []


def test_looks_like_person_rejects_non_names():
    assert looks_like_person("Danilo Bzdok")
    assert looks_like_person("Yasser Iturria-Medina")
    assert not looks_like_person("Department of Neuroscience")
    assert not looks_like_person("our people")
    assert not looks_like_person("Room 302")
    assert not looks_like_person("Bzdok")


def test_generic_address_detection():
    assert is_personal_email("d.bzdok@uni.test")
    assert not is_personal_email("info@uni.test")
    assert not is_personal_email("admissions@uni.test")
    assert not is_personal_email(None)


def test_normalize_name_collapses_whitespace():
    assert normalize_name("  Dr.   Jane   Doe ") == "Jane Doe"


def test_parsed_record_key_is_case_insensitive():
    a = ParsedProfessor(name="Jane Doe", source_url="x", email="Jane@Uni.Test")
    b = ParsedProfessor(name="jane doe", source_url="x", email="jane@uni.test")
    assert a.key() == b.key()
