"""Expense spending policy (Phase 09): admins set caps; out-of-policy items are
flagged (advisory) on the report detail for the approver."""

import pytest


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _activate(auth_client):
    assert (
        await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    ).status_code == 200


async def _member(auth_client, client, email):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": "user"})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "Emp", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


async def _report(auth_client, category="travel", amount="600.00"):
    r = await auth_client.post(
        "/api/v1/expenses",
        json={
            "title": "Trip",
            "currency": "EUR",
            "items": [
                {
                    "spend_date": "2026-05-01",
                    "category": category,
                    "description": "X",
                    "amount": amount,
                    "vat_amount": "0",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_set_and_get_policy(auth_client):
    await _activate(auth_client)
    put = await auth_client.put(
        "/api/v1/expenses/policy",
        json={"max_item_amount": "500", "category_caps": {"travel": "300"}},
    )
    from decimal import Decimal

    assert put.status_code == 200, put.text
    assert Decimal(put.json()["max_item_amount"]) == Decimal("500")
    assert Decimal(put.json()["category_caps"]["travel"]) == Decimal("300")
    got = await auth_client.get("/api/v1/expenses/policy")
    assert Decimal(got.json()["category_caps"]["travel"]) == Decimal("300")


@pytest.mark.asyncio
async def test_non_admin_cannot_set_policy(auth_client, client):
    await _activate(auth_client)
    emp = await _member(auth_client, client, "e@corp.io")
    r = await client.put("/api/v1/expenses/policy", headers=_h(emp), json={"max_item_amount": "1"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_over_limit_flags_violations(auth_client):
    await _activate(auth_client)
    await auth_client.put(
        "/api/v1/expenses/policy",
        json={"max_item_amount": "500", "category_caps": {"travel": "300"}},
    )
    rid = await _report(auth_client, "travel", "600.00")
    detail = (await auth_client.get(f"/api/v1/expenses/{rid}")).json()
    codes = {v["code"] for v in detail["policy_violations"]}
    assert codes == {"over_item_max", "over_category_cap"}


@pytest.mark.asyncio
async def test_within_limits_no_violations(auth_client):
    await _activate(auth_client)
    await auth_client.put(
        "/api/v1/expenses/policy",
        json={"max_item_amount": "1000", "category_caps": {"travel": "1000"}},
    )
    rid = await _report(auth_client, "travel", "200.00")
    detail = (await auth_client.get(f"/api/v1/expenses/{rid}")).json()
    assert detail["policy_violations"] == []


@pytest.mark.asyncio
async def test_no_policy_no_violations(auth_client):
    await _activate(auth_client)
    rid = await _report(auth_client, "travel", "9999.00")
    detail = (await auth_client.get(f"/api/v1/expenses/{rid}")).json()
    assert detail["policy_violations"] == []


@pytest.mark.asyncio
async def test_missing_receipt_over_threshold_flagged(auth_client):
    await _activate(auth_client)
    await auth_client.put("/api/v1/expenses/policy", json={"receipt_required_over": "100"})
    rid = await _report(auth_client, "meals", "250.00")  # no receipt attached
    detail = (await auth_client.get(f"/api/v1/expenses/{rid}")).json()
    codes = {v["code"] for v in detail["policy_violations"]}
    assert "missing_receipt" in codes
