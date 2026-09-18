"""Admin panel flows that go beyond access control."""

from __future__ import annotations

import re


def _current_plan(html: str):
    match = re.search(r"On <strong[^>]*>(.*?)</strong>", html)
    return match.group(1) if match else None


def test_admin_can_grant_and_revoke_a_paid_plan(client, user_factory, login):
    user_factory("grant_admin", is_admin=True)
    target = user_factory("grant_target")
    login("grant_admin")

    page = client.get(f"/admin/users/{target.id}")
    assert page.status_code == 200
    # One button per paid plan; Free is never offered as something to grant.
    assert 'value="pro_monthly"' in page.text
    assert 'name="plan_code" value="free"' not in page.text

    response = client.post(
        f"/admin/users/{target.id}/subscription",
        data={"plan_code": "pro_monthly"},
        follow_redirects=True,
    )
    assert "activated for grant_target" in response.text
    assert _current_plan(response.text) == "Pro · Monthly"

    response = client.post(
        f"/admin/users/{target.id}/subscription",
        data={"plan_code": "__revoke__"},
        follow_redirects=True,
    )
    assert _current_plan(response.text) is None
    assert "on the free plan" in response.text


def test_granting_the_free_plan_is_refused_rather_than_downgrading(client, user_factory, login):
    user_factory("free_admin", is_admin=True)
    target = user_factory("free_target")
    login("free_admin")
    client.post(f"/admin/users/{target.id}/subscription", data={"plan_code": "pro_yearly"})

    response = client.post(
        f"/admin/users/{target.id}/subscription",
        data={"plan_code": "free"},
        follow_redirects=True,
    )
    assert "revoke their subscription" in response.text
    assert _current_plan(response.text) == "Pro · Yearly"
