from __future__ import annotations

import io

from sqlalchemy import select

from app import db as db_module
from app.models import Contact


def test_add_and_list_a_contact(client, user_factory, login):
    user_factory(username="adder")
    login("adder")

    response = client.post(
        "/add",
        data={
            "name": "Ada Lovelace",
            "university": "Analytical Engine Institute",
            "research_focus": "computing",
            "contact_email": "ada@example.com",
            "source_url": "https://example.com",
            "category": "Professors",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get("/professors")
    assert "Ada Lovelace" in page.text
    assert "ada@example.com" in page.text


def test_add_rejects_an_invalid_email(client, user_factory, login):
    user = user_factory(username="baddata")
    login("baddata")
    client.post(
        "/add",
        data={
            "name": "Broken",
            "university": "Nowhere",
            "research_focus": "things",
            "contact_email": "not-an-email",
        },
        follow_redirects=False,
    )
    with db_module.session_scope() as db:
        assert db.execute(
            select(Contact).where(Contact.owner_id == user.id)
        ).scalars().all() == []


def test_toggling_email_sets_and_clears_the_timestamp(client, user_factory, login):
    user = user_factory(username="toggler")
    login("toggler")
    client.post(
        "/add",
        data={
            "name": "Grace Hopper",
            "university": "US Navy",
            "research_focus": "compilers",
            "contact_email": "grace@example.com",
        },
        follow_redirects=False,
    )
    with db_module.session_scope() as db:
        contact = db.execute(select(Contact).where(Contact.owner_id == user.id)).scalar_one()
        contact_id = contact.id
        assert contact.email_sent_at is None

    client.post(f"/toggle-email/{contact_id}", follow_redirects=False)
    with db_module.session_scope() as db:
        contact = db.get(Contact, contact_id)
        assert contact.email_sent is True
        assert contact.email_sent_at is not None

    client.post(f"/toggle-email/{contact_id}", follow_redirects=False)
    with db_module.session_scope() as db:
        contact = db.get(Contact, contact_id)
        assert contact.email_sent is False
        assert contact.email_sent_at is None, "the timestamp should clear when unsent"


def test_search_filters_the_list(client, user_factory, login):
    user_factory(username="searcher")
    login("searcher")
    for name, focus in [("Alan Turing", "computation"), ("Marie Curie", "radioactivity")]:
        client.post(
            "/add",
            data={
                "name": name,
                "university": "Somewhere",
                "research_focus": focus,
                "contact_email": f"{name.split()[0].lower()}@example.com",
            },
            follow_redirects=False,
        )

    hit = client.get("/professors?q=radioactivity")
    assert "Marie Curie" in hit.text
    assert "Alan Turing" not in hit.text


def test_export_returns_a_csv_with_the_users_rows(client, user_factory, login):
    user_factory(username="exporter")
    login("exporter")
    client.post(
        "/add",
        data={
            "name": "Rosalind Franklin",
            "university": "King's College",
            "research_focus": "x-ray crystallography",
            "contact_email": "rosalind@example.com",
        },
        follow_redirects=False,
    )

    response = client.get("/export.csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    body = response.content.decode("utf-8-sig")
    assert "Rosalind Franklin" in body
    assert "rosalind@example.com" in body


def test_import_creates_rows_and_reports_counts(client, user_factory, login):
    user = user_factory(username="importer")
    login("importer")

    csv_body = (
        "Name,University,Research Focus,Contact Email,Category\n"
        "Barbara McClintock,Cold Spring Harbor,genetics,barbara@example.com,Professors\n"
        "Lynn Margulis,Boston University,symbiogenesis,lynn@example.com,Professors\n"
        ",,missing name,nobody@example.com,\n"
    )
    response = client.post(
        "/import.csv",
        files={"file": ("contacts.csv", io.BytesIO(csv_body.encode()), "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "imported=2" in response.headers["location"]
    assert "skipped=1" in response.headers["location"]

    with db_module.session_scope() as db:
        rows = db.execute(select(Contact).where(Contact.owner_id == user.id)).scalars().all()
        assert {r.name for r in rows} == {"Barbara McClintock", "Lynn Margulis"}


def test_import_updates_an_existing_contact_instead_of_duplicating(client, user_factory, login):
    user = user_factory(username="reimporter")
    login("reimporter")
    client.post(
        "/add",
        data={
            "name": "Ada Lovelace",
            "university": "Old Affiliation",
            "research_focus": "old focus",
            "contact_email": "ada@example.com",
        },
        follow_redirects=False,
    )

    csv_body = (
        "Name,University,Research Focus,Contact Email\n"
        "Ada Lovelace,New Affiliation,new focus,ada@example.com\n"
    )
    client.post(
        "/import.csv",
        files={"file": ("contacts.csv", io.BytesIO(csv_body.encode()), "text/csv")},
        follow_redirects=False,
    )

    with db_module.session_scope() as db:
        rows = db.execute(select(Contact).where(Contact.owner_id == user.id)).scalars().all()
        assert len(rows) == 1
        assert rows[0].university == "New Affiliation"


def test_import_handles_a_semicolon_delimited_file(client, user_factory, login):
    """Excel in many locales exports with semicolons."""
    user = user_factory(username="semicolon")
    login("semicolon")
    csv_body = (
        "Name;University;Research Focus;Contact Email\n"
        "Jane Goodall;Cambridge;primatology;jane@example.com\n"
    )
    client.post(
        "/import.csv",
        files={"file": ("contacts.csv", io.BytesIO(csv_body.encode()), "text/csv")},
        follow_redirects=False,
    )
    with db_module.session_scope() as db:
        rows = db.execute(select(Contact).where(Contact.owner_id == user.id)).scalars().all()
        assert len(rows) == 1
        assert rows[0].name == "Jane Goodall"


def test_positions_crud(client, user_factory, login):
    user_factory(username="positioner")
    login("positioner")

    client.post(
        "/positions/add",
        data={
            "field": "PhD Neuroimaging at McGill",
            "link": "https://mcgill.test/phd",
            "category": "Canada",
            "status": "interested",
            "deadline": "2027-01-15",
        },
        follow_redirects=False,
    )
    page = client.get("/positions")
    assert "PhD Neuroimaging at McGill" in page.text
    assert "2027-01-15" in page.text
