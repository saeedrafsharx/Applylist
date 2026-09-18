from __future__ import annotations

from sqlalchemy import select

from app import db as db_module
from app.models import EmailToken, User
from app.services.security import get_password_hash, hash_token, verify_password


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_password_hashing_roundtrip():
    hashed = get_password_hash("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_verify_password_survives_a_garbage_hash():
    # A malformed stored hash must fail the login, not raise.
    assert verify_password("anything", "not-a-bcrypt-hash") is False
    assert verify_password("anything", "") is False


def test_register_creates_unverified_user_and_a_token(client):
    response = client.post(
        "/register",
        data={
            "username": "newbie",
            "email": "newbie@example.com",
            "password": "testpass123",
            "password_confirm": "testpass123",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/verify-email"

    with db_module.session_scope() as db:
        user = db.execute(select(User).where(User.username == "newbie")).scalar_one()
        assert user.is_email_verified is False
        token = db.execute(
            select(EmailToken).where(EmailToken.user_id == user.id)
        ).scalar_one()
        assert token.purpose == EmailToken.PURPOSE_VERIFY
        assert token.used_at is None


def test_register_rejects_duplicate_username(client, user_factory):
    user_factory(username="taken")
    response = client.post(
        "/register",
        data={
            "username": "taken",
            "email": "different@example.com",
            "password": "testpass123",
            "password_confirm": "testpass123",
        },
    )
    assert response.status_code == 400
    assert "already registered" in response.text


def test_register_rejects_mismatched_confirmation(client):
    response = client.post(
        "/register",
        data={
            "username": "mismatch",
            "email": "mismatch@example.com",
            "password": "testpass123",
            "password_confirm": "somethingelse",
        },
    )
    assert response.status_code == 400
    assert "don&#39;t match" in response.text or "don't match" in response.text


def test_register_rejects_a_short_password(client):
    response = client.post(
        "/register",
        data={
            "username": "shorty",
            "email": "shorty@example.com",
            "password": "abc",
            "password_confirm": "abc",
        },
    )
    assert response.status_code == 400


def test_unverified_user_is_bounced_from_the_dashboard(client, user_factory, login):
    user_factory(username="unverified", is_email_verified=False)
    login("unverified")
    response = client.get("/professors", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/verify-email"


def test_verification_link_verifies_and_is_single_use(client, user_factory):
    from datetime import datetime, timedelta, timezone

    user = user_factory(username="tokenuser", is_email_verified=False)
    raw = "a-known-test-token"
    with db_module.session_scope() as db:
        db.add(
            EmailToken(
                user_id=user.id,
                token_hash=hash_token(raw),
                purpose=EmailToken.PURPOSE_VERIFY,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                created_at=datetime.now(timezone.utc),
            )
        )

    first = client.get(f"/verify-email/{raw}", follow_redirects=False)
    assert first.status_code == 303
    with db_module.session_scope() as db:
        assert db.get(User, user.id).is_email_verified is True

    # Replaying the same link must not work.
    second = client.get(f"/verify-email/{raw}", follow_redirects=False)
    assert second.status_code == 400


def test_expired_verification_token_is_rejected(client, user_factory):
    from datetime import datetime, timedelta, timezone

    user = user_factory(username="expired", is_email_verified=False)
    raw = "an-expired-token"
    with db_module.session_scope() as db:
        db.add(
            EmailToken(
                user_id=user.id,
                token_hash=hash_token(raw),
                purpose=EmailToken.PURPOSE_VERIFY,
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                created_at=datetime.now(timezone.utc) - timedelta(hours=25),
            )
        )
    response = client.get(f"/verify-email/{raw}", follow_redirects=False)
    assert response.status_code == 400
    with db_module.session_scope() as db:
        assert db.get(User, user.id).is_email_verified is False


def test_login_rejects_a_bad_password(client, user_factory):
    user_factory(username="realuser")
    response = client.post(
        "/login", data={"username": "realuser", "password": "wrong"}
    )
    assert response.status_code == 401


def test_login_accepts_the_email_address_too(client, user_factory):
    user_factory(username="byemail", email="byemail@example.com")
    response = client.post(
        "/login",
        data={"username": "byemail@example.com", "password": "testpass123"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_suspended_user_cannot_sign_in(client, user_factory):
    user_factory(username="banned", is_active=False)
    response = client.post(
        "/login", data={"username": "banned", "password": "testpass123"}
    )
    assert response.status_code == 403


def test_forgot_password_does_not_reveal_whether_an_account_exists(client, user_factory):
    user_factory(username="known", email="known@example.com")
    real = client.post("/forgot-password", data={"email": "known@example.com"})
    fake = client.post("/forgot-password", data={"email": "nobody@example.com"})
    assert real.status_code == fake.status_code == 200
    assert "Check your inbox" in real.text and "Check your inbox" in fake.text
