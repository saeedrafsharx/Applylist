"""
Test fixtures.

These tests run against a real Postgres because the app relies on Postgres-only
features (JSONB columns, `date_trunc` in the admin aggregates, `ilike`). Point
TEST_DATABASE_URL at a throwaway database; the schema is built and dropped
around the whole session.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://applylist:applylist@localhost:5432/applylist_test",
)
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("BASE_URL", "http://testserver")
# Keep the suite offline: no SMTP, no gateway, no model calls.
os.environ["SMTP_HOST"] = ""
os.environ["ZARINPAL_MERCHANT_ID"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["ADMIN_EMAIL"] = ""


@pytest.fixture(scope="session")
def engine():
    from app.models import Base

    eng = create_engine(TEST_DB_URL, future=True)
    with eng.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    """A session that rolls back everything the test did."""
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, expire_on_commit=False, future=True)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(engine):
    """A TestClient wired to the test database, with seed data applied once."""
    from fastapi.testclient import TestClient

    from app import db as db_module
    from app.bootstrap import seed
    from app.main import app

    db_module.engine = engine
    db_module.SessionLocal.configure(bind=engine)

    with db_module.session_scope() as session:
        seed(session)

    with TestClient(app) as c:
        yield c


@pytest.fixture
def user_factory(engine):
    """Create verified users directly, skipping the email round-trip."""
    from datetime import datetime, timezone

    from app import db as db_module
    from app.models import User
    from app.services.security import get_password_hash

    created: list[int] = []
    counter = {"n": 0}

    def make(username: str = "", password: str = "testpass123", **kwargs):
        counter["n"] += 1
        username = username or f"user{counter['n']}"
        with db_module.session_scope() as session:
            user = User(
                username=username,
                email=kwargs.pop("email", f"{username}@example.com"),
                password_hash=get_password_hash(password),
                is_email_verified=kwargs.pop("is_email_verified", True),
                email_verified_at=datetime.now(timezone.utc),
                **kwargs,
            )
            session.add(user)
            session.flush()
            created.append(user.id)
            return user

    return make


@pytest.fixture
def login(client):
    def do(username: str, password: str = "testpass123"):
        response = client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text
        return response

    return do
