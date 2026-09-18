"""
The rules that keep one user's data away from another, and paid features away
from unpaid accounts. These are the tests worth having.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import db as db_module
from app.models import Contact, Plan, Position, User
from app.services.billing import grant_subscription


@pytest.fixture
def pro_user(user_factory):
    user = user_factory(username="prouser")
    with db_module.session_scope() as db:
        plan = db.execute(select(Plan).where(Plan.code == "pro_monthly")).scalar_one()
        grant_subscription(db, db.get(User, user.id), plan)
    return user


def _contact_for(owner_id: int, name: str = "Someone Else") -> int:
    with db_module.session_scope() as db:
        contact = Contact(
            owner_id=owner_id,
            name=name,
            university="Elsewhere",
            research_focus="things",
            contact_email="someone@example.com",
            source_url="#",
        )
        db.add(contact)
        db.flush()
        return contact.id


# ── ownership ───────────────────────────────────────────────────


def test_a_user_cannot_open_another_users_contact(client, user_factory, login):
    owner = user_factory(username="owner")
    user_factory(username="intruder")
    contact_id = _contact_for(owner.id)

    login("intruder")
    response = client.get(f"/edit/{contact_id}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/professors"


def test_a_user_cannot_delete_another_users_contact(client, user_factory, login):
    owner = user_factory(username="owner2")
    user_factory(username="intruder2")
    contact_id = _contact_for(owner.id)

    login("intruder2")
    client.post(f"/delete/{contact_id}", follow_redirects=False)

    with db_module.session_scope() as db:
        assert db.get(Contact, contact_id) is not None, "the contact was deleted by a stranger"


def test_a_user_cannot_toggle_another_users_contact(client, user_factory, login):
    owner = user_factory(username="owner3")
    user_factory(username="intruder3")
    contact_id = _contact_for(owner.id)

    login("intruder3")
    client.post(f"/toggle-email/{contact_id}", follow_redirects=False)

    with db_module.session_scope() as db:
        assert db.get(Contact, contact_id).email_sent is False


def test_a_users_list_only_shows_their_own_contacts(client, user_factory, login):
    owner = user_factory(username="owner4")
    user_factory(username="other4")
    _contact_for(owner.id, name="Private Person")

    login("other4")
    response = client.get("/professors")
    assert "Private Person" not in response.text


def test_a_user_cannot_edit_another_users_position(client, user_factory, login):
    owner = user_factory(username="powner")
    user_factory(username="pintruder")
    with db_module.session_scope() as db:
        position = Position(owner_id=owner.id, field="Secret post", link="https://x.test")
        db.add(position)
        db.flush()
        position_id = position.id

    login("pintruder")
    response = client.get(f"/positions/edit/{position_id}", follow_redirects=False)
    assert response.status_code == 303


# ── anonymous access ────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    ["/professors", "/positions", "/database", "/assistant", "/billing", "/admin"],
)
def test_signed_out_users_are_sent_to_login(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# ── paid gating ─────────────────────────────────────────────────


def test_free_user_sees_the_database_paywall_not_the_data(client, user_factory, login):
    user_factory(username="freeuser")
    login("freeuser")

    landing = client.get("/database")
    assert landing.status_code == 200
    assert "Pro feature" in landing.text

    # The search page itself must bounce, not just hide the link.
    search = client.get("/database/professors", follow_redirects=False)
    assert search.status_code == 303
    assert search.headers["location"] == "/database"


def test_free_user_cannot_reach_the_assistant(client, user_factory, login):
    user_factory(username="freeuser2")
    login("freeuser2")

    landing = client.get("/assistant")
    assert landing.status_code == 200
    assert "Unlock with Pro" in landing.text

    # Deep links into the feature must redirect to pricing.
    for path in ("/assistant/new", "/assistant/draft"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/pricing")


def test_paid_user_reaches_the_database(client, pro_user, login):
    login("prouser")
    response = client.get("/database/professors")
    assert response.status_code == 200
    assert "Faculty search" in response.text


def test_paid_user_reaches_the_assistant(client, pro_user, login):
    login("prouser")
    response = client.get("/assistant/new", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/assistant/c/")


# ── admin gating ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    ["/admin", "/admin/users", "/admin/activity", "/admin/payments",
     "/admin/catalog", "/admin/scraper", "/admin/plans", "/admin/takedowns"],
)
def test_a_normal_user_cannot_reach_any_admin_page(client, user_factory, login, path):
    user_factory(username=f"plain{abs(hash(path)) % 10000}")
    login(f"plain{abs(hash(path)) % 10000}")
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_a_paid_user_is_still_not_an_admin(client, pro_user, login):
    login("prouser")
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_admin_reaches_the_panel(client, user_factory, login):
    user_factory(username="theboss", is_admin=True)
    login("theboss")
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Total users" in response.text


def test_admin_cannot_delete_their_own_account(client, user_factory, login):
    admin = user_factory(username="selfdelete", is_admin=True)
    login("selfdelete")
    client.post(
        f"/admin/users/{admin.id}/delete",
        data={"confirm": "selfdelete"},
        follow_redirects=False,
    )
    with db_module.session_scope() as db:
        assert db.get(User, admin.id) is not None


def test_admin_delete_requires_the_username_typed_exactly(client, user_factory, login):
    user_factory(username="bigboss", is_admin=True)
    victim = user_factory(username="victim")
    login("bigboss")

    client.post(
        f"/admin/users/{victim.id}/delete",
        data={"confirm": "wrong-name"},
        follow_redirects=False,
    )
    with db_module.session_scope() as db:
        assert db.get(User, victim.id) is not None, "deleted without a matching confirmation"

    client.post(
        f"/admin/users/{victim.id}/delete",
        data={"confirm": "victim"},
        follow_redirects=False,
    )
    with db_module.session_scope() as db:
        assert db.get(User, victim.id) is None


# ── open redirect ───────────────────────────────────────────────


def test_safe_back_rejects_an_attacker_supplied_referer():
    """
    Several actions bounce the user back to the page they came from. Referer is
    client-controlled, so it must never become an off-site redirect.
    """
    from urllib.parse import urlparse

    from app.deps import safe_back

    class FakeRequest:
        def __init__(self, referer):
            self.headers = {"referer": referer} if referer else {}
            self.url = urlparse("https://applylist.test/database/professors")

    assert safe_back(FakeRequest("https://evil.test/phish"), "/fallback") == "/fallback"
    assert safe_back(FakeRequest("//evil.test/phish"), "/fallback") == "/fallback"
    assert safe_back(FakeRequest(""), "/fallback") == "/fallback"
    # A same-site path is honoured, query string and all.
    assert safe_back(
        FakeRequest("https://applylist.test/database/professors?q=brain"), "/fallback"
    ) == "/database/professors?q=brain"
