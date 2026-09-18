from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import db as db_module
from app.models import Payment, Plan, Subscription, User
from app.services.billing import (
    BillingError,
    active_subscription,
    grant_subscription,
    revoke_subscription,
    user_has_feature,
    verify_payment,
)


@pytest.fixture
def plans(client):
    with db_module.session_scope() as db:
        return {
            p.code: p.id
            for p in db.execute(select(Plan)).scalars()
        }


def test_seeded_plans_exist(plans):
    assert {"free", "pro_monthly", "pro_yearly"} <= set(plans)


def test_granting_a_plan_activates_the_features(client, user_factory, plans):
    user = user_factory(username="grantee")
    with db_module.session_scope() as db:
        plan = db.get(Plan, plans["pro_monthly"])
        sub = grant_subscription(db, db.get(User, user.id), plan)
        assert sub.is_active
        assert sub.expires_at is not None
        assert user_has_feature(db, db.get(User, user.id), "ai") is True
        assert user_has_feature(db, db.get(User, user.id), "catalog") is True


def test_free_plan_grants_nothing(client, user_factory, plans):
    user = user_factory(username="freebie")
    with db_module.session_scope() as db:
        plan = db.get(Plan, plans["free"])
        grant_subscription(db, db.get(User, user.id), plan)
        assert user_has_feature(db, db.get(User, user.id), "ai") is False
        assert user_has_feature(db, db.get(User, user.id), "catalog") is False


def test_renewing_the_same_plan_extends_rather_than_replaces(client, user_factory, plans):
    user = user_factory(username="renewer")
    with db_module.session_scope() as db:
        plan = db.get(Plan, plans["pro_monthly"])
        target = db.get(User, user.id)
        first = grant_subscription(db, target, plan)
        first_expiry = first.expires_at

    with db_module.session_scope() as db:
        plan = db.get(Plan, plans["pro_monthly"])
        second = grant_subscription(db, db.get(User, user.id), plan)
        assert second.expires_at > first_expiry, "renewal should add time, not reset it"

    with db_module.session_scope() as db:
        count = len(
            db.execute(
                select(Subscription).where(Subscription.user_id == user.id)
            ).scalars().all()
        )
        assert count == 1, "renewing must not stack a second subscription row"


def test_switching_plans_supersedes_the_old_one(client, user_factory, plans):
    user = user_factory(username="switcher")
    with db_module.session_scope() as db:
        grant_subscription(db, db.get(User, user.id), db.get(Plan, plans["pro_monthly"]))
    with db_module.session_scope() as db:
        grant_subscription(db, db.get(User, user.id), db.get(Plan, plans["pro_yearly"]))

    with db_module.session_scope() as db:
        current = active_subscription(db, user.id)
        assert current.plan.code == "pro_yearly"
        actives = db.execute(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.status == Subscription.STATUS_ACTIVE,
            )
        ).scalars().all()
        assert len(actives) == 1


def test_an_expired_subscription_stops_granting_features(client, user_factory, plans):
    user = user_factory(username="lapsed")
    with db_module.session_scope() as db:
        sub = grant_subscription(db, db.get(User, user.id), db.get(Plan, plans["pro_monthly"]))
        sub.expires_at = datetime.now(timezone.utc) - timedelta(days=1)

    with db_module.session_scope() as db:
        assert active_subscription(db, user.id) is None
        assert user_has_feature(db, db.get(User, user.id), "ai") is False


def test_revoking_removes_access(client, user_factory, plans):
    user = user_factory(username="revoked")
    with db_module.session_scope() as db:
        grant_subscription(db, db.get(User, user.id), db.get(Plan, plans["pro_monthly"]))
    with db_module.session_scope() as db:
        assert revoke_subscription(db, db.get(User, user.id)) is True
    with db_module.session_scope() as db:
        assert user_has_feature(db, db.get(User, user.id), "ai") is False


def test_admins_pass_feature_checks_without_a_subscription(client, user_factory):
    user = user_factory(username="adminfeat", is_admin=True)
    with db_module.session_scope() as db:
        assert user_has_feature(db, db.get(User, user.id), "ai") is True
        assert user_has_feature(db, db.get(User, user.id), "catalog") is True


def test_subscribing_without_a_gateway_configured_fails_cleanly(client, user_factory, login):
    """ZARINPAL_MERCHANT_ID is empty in the suite - the user must get a message, not a 500."""
    user_factory(username="wouldpay")
    login("wouldpay")
    response = client.post("/billing/subscribe/pro_monthly", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/pricing"


def test_a_verified_payment_is_not_settled_twice(client, user_factory, plans):
    """
    The callback URL is a plain GET the user can refresh. Re-verifying an
    already-paid payment must be a no-op, or a refresh buys a free month.
    """
    user = user_factory(username="doublepay")
    with db_module.session_scope() as db:
        payment = Payment(
            user_id=user.id,
            plan_id=plans["pro_monthly"],
            provider="zarinpal",
            amount=990_000,
            status=Payment.STATUS_PAID,
            authority="A00000000000000000000000000000already",
            ref_id="123456",
            paid_at=datetime.now(timezone.utc),
        )
        db.add(payment)
        db.flush()

        settled, ok, message = verify_payment(db, payment.authority, "OK")
        assert ok is True
        assert "already" in message.lower()
        assert settled.id == payment.id


def test_a_canceled_callback_marks_the_payment_canceled(client, user_factory, plans):
    user = user_factory(username="cancelled")
    with db_module.session_scope() as db:
        payment = Payment(
            user_id=user.id,
            plan_id=plans["pro_monthly"],
            provider="zarinpal",
            amount=990_000,
            status=Payment.STATUS_PENDING,
            authority="A00000000000000000000000000000cancel",
        )
        db.add(payment)
        db.flush()

        settled, ok, _ = verify_payment(db, payment.authority, "NOK")
        assert ok is False
        assert settled.status == Payment.STATUS_CANCELED


def test_verifying_an_unknown_authority_raises(client):
    with db_module.session_scope() as db:
        with pytest.raises(BillingError):
            verify_payment(db, "A0000000000000000000000000000unknown", "OK")


def test_price_conversion_rial_to_toman(client, plans):
    with db_module.session_scope() as db:
        plan = db.get(Plan, plans["pro_monthly"])
        assert plan.price_rial == 990_000
        assert plan.price_toman == 99_000
