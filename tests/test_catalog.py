from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app import db as db_module
from app.models import CatalogProfessor, CatalogUniversity, Contact, Plan, TakedownRequest, User
from app.routers.catalog import mask_email
from app.services.billing import grant_subscription


@pytest.fixture
def catalog_entry(client):
    with db_module.session_scope() as db:
        university = db.execute(
            select(CatalogUniversity).where(CatalogUniversity.slug == "mcgill")
        ).scalar_one()
        entry = CatalogProfessor(
            university_id=university.id,
            name="Test Supervisor",
            email="test.supervisor@uni.test",
            research_focus="computational neuroimaging",
            source_url="https://uni.test/faculty",
            scraped_at=datetime.now(timezone.utc),
            is_published=True,
        )
        db.add(entry)
        db.flush()
        return entry.id


@pytest.fixture
def pro_user(user_factory):
    user = user_factory(username="catalogpro")
    with db_module.session_scope() as db:
        plan = db.execute(select(Plan).where(Plan.code == "pro_monthly")).scalar_one()
        grant_subscription(db, db.get(User, user.id), plan)
    return user


def test_mask_email_hides_the_address_but_keeps_the_domain():
    masked = mask_email("danilo.bzdok@mcgill.ca")
    assert masked.endswith("@mcgill.ca")
    assert "bzdok" not in masked
    assert masked.startswith("d")
    assert mask_email(None) == "—"


def test_the_free_preview_never_leaks_a_real_address(client, user_factory, login, catalog_entry):
    user_factory(username="peeker")
    login("peeker")
    response = client.get("/database")
    assert response.status_code == 200
    assert "test.supervisor@uni.test" not in response.text
    assert "Test Supervisor" in response.text  # the name is the teaser


def test_paid_user_sees_the_real_address(client, pro_user, login, catalog_entry):
    login("catalogpro")
    response = client.get("/database/professors")
    assert "test.supervisor@uni.test" in response.text


def test_saving_a_catalog_entry_copies_it_into_the_users_contacts(
    client, pro_user, login, catalog_entry
):
    login("catalogpro")
    response = client.post(f"/database/save/{catalog_entry}", follow_redirects=False)
    assert response.status_code == 303

    with db_module.session_scope() as db:
        contact = db.execute(
            select(Contact).where(Contact.owner_id == pro_user.id)
        ).scalar_one()
        assert contact.name == "Test Supervisor"
        assert contact.contact_email == "test.supervisor@uni.test"
        assert contact.catalog_professor_id == catalog_entry


def test_saving_the_same_entry_twice_does_not_duplicate(client, pro_user, login, catalog_entry):
    login("catalogpro")
    client.post(f"/database/save/{catalog_entry}", follow_redirects=False)
    client.post(f"/database/save/{catalog_entry}", follow_redirects=False)

    with db_module.session_scope() as db:
        contacts = db.execute(
            select(Contact).where(Contact.owner_id == pro_user.id)
        ).scalars().all()
        assert len(contacts) == 1


def test_a_free_user_cannot_save_a_catalog_entry(client, user_factory, login, catalog_entry):
    user = user_factory(username="freeloader")
    login("freeloader")
    client.post(f"/database/save/{catalog_entry}", follow_redirects=False)

    with db_module.session_scope() as db:
        contacts = db.execute(
            select(Contact).where(Contact.owner_id == user.id)
        ).scalars().all()
        assert contacts == []


def test_removed_entries_disappear_from_search(client, pro_user, login, catalog_entry):
    with db_module.session_scope() as db:
        db.get(CatalogProfessor, catalog_entry).is_removed = True

    login("catalogpro")
    response = client.get("/database/professors")
    assert "Test Supervisor" not in response.text


# ── takedown ────────────────────────────────────────────────────


def test_the_takedown_form_is_reachable_without_an_account(client):
    """The people listed are not our users; they must not need one to opt out."""
    response = client.get("/database/takedown")
    assert response.status_code == 200
    assert "Remove a listing" in response.text


def test_a_takedown_request_unpublishes_immediately(client, catalog_entry):
    response = client.post(
        "/database/takedown",
        data={
            "subject_name": "Test Supervisor",
            "requester_email": "test.supervisor@uni.test",
            "reason": "Please remove me.",
            "professor_id": catalog_entry,
        },
    )
    assert response.status_code == 200
    assert "Request received" in response.text

    with db_module.session_scope() as db:
        assert db.get(CatalogProfessor, catalog_entry).is_published is False
        request_row = db.execute(select(TakedownRequest)).scalars().first()
        assert request_row.status == TakedownRequest.STATUS_OPEN
        assert request_row.professor_id == catalog_entry
