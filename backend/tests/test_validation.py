"""Opt-in data validation: AI (automated) and human review, both off by default."""
import pytest


def _payload(number="V-1", unit="100.00", qty="1", tax="0", status="pending"):
    return {
        "vendor_name": "Acme", "invoice_number": number, "issue_date": "2026-03-01",
        "currency": "EUR", "status": status,
        "line_items": [
            {"description": "svc", "category": "c", "quantity": qty, "unit_price": unit, "tax_rate": tax},
        ],
    }


async def _set_settings(auth_client, ai=None, human=None):
    body = {}
    if ai is not None:
        body["ai_validation_enabled"] = ai
    if human is not None:
        body["human_validation_enabled"] = human
    return await auth_client.put("/api/v1/settings/validation", json=body)


@pytest.mark.asyncio
async def test_defaults_off_status_none(auth_client):
    s = (await auth_client.get("/api/v1/settings/validation")).json()
    assert s == {"ai_validation_enabled": False, "human_validation_enabled": False}

    inv = (await auth_client.post("/api/v1/invoices", json=_payload())).json()
    assert inv["validation_status"] == "none"


@pytest.mark.asyncio
async def test_ai_only_passes_clean_invoice(auth_client):
    await _set_settings(auth_client, ai=True)
    inv = (await auth_client.post("/api/v1/invoices", json=_payload())).json()
    assert inv["validation_status"] == "passed"
    assert inv["validation_findings"] == []


@pytest.mark.asyncio
async def test_ai_flags_duplicate_and_line_math(auth_client):
    await _set_settings(auth_client, ai=True)
    await auth_client.post("/api/v1/invoices", json=_payload("DUP-1"))
    # same vendor + number → duplicate (error); qty×unit mismatch → warning
    bad = {
        "vendor_name": "Acme", "invoice_number": "DUP-1", "issue_date": "2026-03-01",
        "line_items": [
            {"description": "x", "category": "c", "quantity": "3", "unit_price": "100.00",
             "amount": "250.00", "tax_rate": "0"},
        ],
    }
    inv = (await auth_client.post("/api/v1/invoices", json=bad)).json()
    assert inv["validation_status"] == "flagged"
    codes = {f["code"] for f in inv["validation_findings"]}
    assert "duplicate" in codes
    assert "line_math" in codes


@pytest.mark.asyncio
async def test_human_review_gate_and_approve(auth_client):
    await _set_settings(auth_client, human=True)
    inv = (await auth_client.post("/api/v1/invoices", json=_payload("H-1"))).json()
    assert inv["validation_status"] == "pending"

    # shows up in the pending queue
    queue = (await auth_client.get("/api/v1/invoices?validation_status=pending")).json()
    assert queue["total"] == 1

    approved = (await auth_client.post(
        f"/api/v1/invoices/{inv['id']}/validate", json={"action": "approve", "note": "looks good"}
    )).json()
    assert approved["validation_status"] == "approved"
    assert approved["validated_by"] == "owner@acme.io"


@pytest.mark.asyncio
async def test_both_on_ai_findings_plus_human_gate(auth_client):
    await _set_settings(auth_client, ai=True, human=True)
    # future date → warning, but human still gates it at pending
    p = _payload("B-1")
    p["issue_date"] = "2999-01-01"
    inv = (await auth_client.post("/api/v1/invoices", json=p)).json()
    assert inv["validation_status"] == "pending"
    assert any(f["code"] == "future_date" for f in inv["validation_findings"])

    rejected = (await auth_client.post(
        f"/api/v1/invoices/{inv['id']}/validate", json={"action": "reject"}
    )).json()
    assert rejected["validation_status"] == "rejected"


@pytest.mark.asyncio
async def test_settings_owner_only(client):
    # a fresh org owner can change settings; verify PUT works for owner
    r = await client.post("/api/v1/auth/register", json={
        "organization_name": "Z", "name": "z", "email": "z@zed.io", "password": "supersecret"})
    tok = r.json()["token"]["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    upd = await client.put("/api/v1/settings/validation", json={"ai_validation_enabled": True}, headers=h)
    assert upd.status_code == 200
    assert upd.json()["ai_validation_enabled"] is True
