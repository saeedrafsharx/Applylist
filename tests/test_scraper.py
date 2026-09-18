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


# ── obfuscated addresses, tables, pagination, auto ──────────────

from app.services.scraper.parsers import (  # noqa: E402
    decode_cfemail,
    deobfuscate,
    emails_in,
    find_next_page,
    parse_auto,
    parse_table_directory,
)

# Shaped like McGill's IPN list: a table, addresses disguised as "[at]".
TABLE_HTML = """
<html><body><main>
  <p>Questions can go to projects.ipn [at] mcgill.ca (IPN Project Administrator)</p>
  <table>
    <tr><th>NAME</th><th>E-MAIL</th><th>WEBSITE</th><th>NOTES</th></tr>
    <tr><td>Dr. Alyson Fournier</td><td>alyson.fournier<br>[at]<br>mcgill.ca</td>
        <td><a href="https://fournierlab.mcgill.ca/">https://fournierlab.mcgill.ca/</a></td>
        <td>Research in molecular and cellular mechanisms underlying axon regeneration</td></tr>
    <tr><td>Dr. Danilo Bzdok</td><td>danilo.bzdok [at] mcgill [dot] ca</td>
        <td><a href="/neuro/danilo-bzdok">profile</a></td>
        <td>Looking for PhD students with skills in pattern-learning analysis.</td></tr>
    <tr><td>Graduate Office</td><td>grad [at] mcgill.ca</td><td></td><td></td></tr>
  </table>
</main></body></html>
"""

# A Drupal-style card list whose rows carry mailto links, with a pager.
CARDS_HTML = """
<html><body><main>
  <div class="view-content">
    <div class="views-row">
      <a href="/neuroscience/people/al-jin"><img src="x.jpg"></a>
      <h3><a href="/neuroscience/people/al-jin">Al Jin</a></h3>
      <div>Neurology</div>
      <a href="mailto:ayj@queensu.ca">ayj@queensu.ca</a>
      <a href="https://deptmed.queensu.ca/people/albert-jin">Department of Medicine</a>
      <p>Research interests: stroke, cerebral injury, robotics, motor control</p>
    </div>
    <div class="views-row">
      <h3><a href="/neuroscience/people/anita-tusche">Anita Tusche</a></h3>
      <a href="mailto:Anita.Tusche@queensu.ca">Anita.Tusche@queensu.ca</a>
      <p>Research interests: decision making, neuroeconomics</p>
    </div>
  </div>
  <nav class="pager"><ul><li class="pager__item--next"><a href="?page=1" rel="next">Next ›</a></li></ul></nav>
</main></body></html>
"""


def test_deobfuscation_handles_common_disguises():
    assert "jane.doe@uni.edu" in emails_in("jane.doe [at] uni [dot] edu")
    assert "jane.doe@uni.edu" in emails_in("jane.doe(at)uni.edu")
    assert "jane.doe@uni.edu" in emails_in("jane.doe {at} uni.edu")
    assert "jane.doe@uni.edu" in emails_in("jane.doe at uni dot edu")
    # Ordinary prose that happens to contain "at" is left alone.
    assert deobfuscate("Look at the results") == "Look at the results"
    assert emails_in("We meet at noon in the lab") == []


def test_cloudflare_protected_addresses_are_decoded():
    # "a@b.co" XOR-ed with key 0x42, as Cloudflare's email protection encodes it.
    key = 0x42
    encoded = f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in "a@b.co")
    assert decode_cfemail(encoded) == "a@b.co"
    assert decode_cfemail("zz") is None


def test_table_parser_reads_columns_by_header():
    rows = parse_table_directory(TABLE_HTML, "https://www.mcgill.ca/ipn/list")
    by_email = {r.email: r for r in rows}
    assert set(by_email) == {"alyson.fournier@mcgill.ca", "danilo.bzdok@mcgill.ca"}

    fournier = by_email["alyson.fournier@mcgill.ca"]
    assert fournier.name == "Alyson Fournier"
    assert fournier.profile_url == "https://fournierlab.mcgill.ca/"
    assert "axon regeneration" in fournier.research_focus

    # Relative website links resolve against the page.
    assert by_email["danilo.bzdok@mcgill.ca"].profile_url == "https://www.mcgill.ca/neuro/danilo-bzdok"


def test_card_parser_scopes_each_card_to_one_person():
    rows = parse_mailto_directory(CARDS_HTML, "https://www.queensu.ca/neuroscience/people")
    by_email = {r.email: r for r in rows}
    assert set(by_email) == {"ayj@queensu.ca", "anita.tusche@queensu.ca"}

    jin = by_email["ayj@queensu.ca"]
    assert jin.name == "Al Jin"
    assert jin.research_focus == "stroke, cerebral injury, robotics, motor control"
    assert jin.department == "Department of Medicine"
    # The link whose text is the name wins over other links in the card.
    assert jin.profile_url == "https://www.queensu.ca/neuroscience/people/al-jin"
    # Anita's interests must not bleed into Al's card, or vice versa.
    assert "neuroeconomics" not in (jin.research_focus or "")


def test_auto_picks_the_strategy_that_fits():
    assert {r.email for r in parse_auto(TABLE_HTML, "https://www.mcgill.ca/x")} == {
        "alyson.fournier@mcgill.ca", "danilo.bzdok@mcgill.ca",
    }
    assert len(parse_auto(CARDS_HTML, "https://www.queensu.ca/x")) == 2
    # With no addresses anywhere, it falls back to following profile links.
    names = {r.name for r in parse_auto(INDEX_HTML, "https://uni.test/faculty")}
    assert {"Gunnar Blohm", "Jason Gallivan"} <= names


def test_next_page_is_found_and_stays_on_site():
    assert find_next_page(CARDS_HTML, "https://www.queensu.ca/neuroscience/people") == (
        "https://www.queensu.ca/neuroscience/people?page=1"
    )
    offsite = '<a rel="next" href="https://elsewhere.test/page2">Next</a>'
    assert find_next_page(offsite, "https://uni.test/people") is None
    assert find_next_page("<p>no pager</p>", "https://uni.test/people") is None


def test_names_containing_blocked_words_as_substrings_survive():
    """Filtering is by whole word: "Homer" is not "home", "Newsome" is not "news"."""
    assert looks_like_person("Homer Simpson")
    assert looks_like_person("Gavin Newsome")
    assert not looks_like_person("Home Page")
    assert not looks_like_person("View Profile")
    assert not looks_like_person("Associate Professor")


def test_postnominals_and_stacked_honorifics_are_stripped():
    assert normalize_name("Prof. Dr. Jane Doe, PhD") == "Jane Doe"
    assert normalize_name("Danilo Bzdok MD, PhD") == "Danilo Bzdok"
