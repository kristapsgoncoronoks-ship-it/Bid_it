"""Partner pre-invoicing workflow (contract / acceptance act gating) and
partner-level penalty invoicing (gated on a signed contract)."""
import pytest

ISSUER = {
    "legal_name": "InvoiceIQ Demo BV", "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678", "address_line1": "Keizersgracht 1",
    "city": "Amsterdam", "postal_code": "1015 CJ", "country": "NL",
    "iban": "NL91ABNA0417164300", "bic": "ABNANL2A", "email": "billing@invoiceiq.test",
}


async def _activate(auth_client):
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})


def _invoice(partner_id, price="1000.00", *, issue, due, penalty=None):
    body = {
        "partner_id": partner_id, "buyer_name": "ignored-when-partner",
        "issue_date": issue, "due_date": due,
        "lines": [{"description": "Service", "quantity": "1", "unit_price": price, "vat_rate": "0"}],
    }
    if penalty is not None:
        body["penalty_rate"] = penalty
    return body


async def _partner(auth_client, **kw):
    body = {"name": "Meridian Freight", "email": "ap@meridian.io", **kw}
    return (await auth_client.post("/api/v1/partners", json=body)).json()


@pytest.mark.asyncio
async def test_workflow_gate_blocks_until_signed(auth_client):
    await _activate(auth_client)
    p = await _partner(auth_client, requires_contract=True, requires_acceptance=True)
    assert p["readiness"]["ready"] is False
    assert set(p["readiness"]["missing"]) == {"contract", "acceptance_act"}

    # Issuing is blocked while documents are unsigned.
    r = await auth_client.post("/api/v1/issued", json=_invoice(p["id"], issue="2026-06-01", due="2026-06-30"))
    assert r.status_code == 409
    assert "awaiting signed" in r.json()["detail"]

    # Add + sign the contract; acceptance still missing → still blocked.
    c = (await auth_client.post(f"/api/v1/partners/{p['id']}/documents",
         json={"kind": "contract", "title": "Framework agreement"})).json()
    await auth_client.post(f"/api/v1/partners/{p['id']}/documents/{c['id']}/sign", json={"signed_by": "CEO"})
    r = await auth_client.post("/api/v1/issued", json=_invoice(p["id"], issue="2026-06-01", due="2026-06-30"))
    assert r.status_code == 409

    # Sign the acceptance act → ready → issuing succeeds and links the partner.
    a = (await auth_client.post(f"/api/v1/partners/{p['id']}/documents",
         json={"kind": "acceptance_act", "title": "Acceptance act #1"})).json()
    signed = (await auth_client.post(f"/api/v1/partners/{p['id']}/documents/{a['id']}/sign", json={})).json()
    assert signed["readiness"]["ready"] is True

    ok = await auth_client.post("/api/v1/issued", json=_invoice(p["id"], issue="2026-06-01", due="2026-06-30"))
    assert ok.status_code == 201, ok.text
    assert ok.json()["partner_id"] == p["id"]
    assert ok.json()["kind"] == "standard"


@pytest.mark.asyncio
async def test_no_workflow_partner_issues_immediately(auth_client):
    await _activate(auth_client)
    p = await _partner(auth_client)  # no requirements
    assert p["readiness"]["ready"] is True
    r = await auth_client.post("/api/v1/issued", json=_invoice(p["id"], issue="2026-06-01", due="2026-06-30"))
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_penalty_invoice_requires_contract_then_generates(auth_client):
    await _activate(auth_client)
    # Penalty enabled, contract required, penalty rate 12% p.a.
    p = await _partner(auth_client, requires_contract=True, penalty_enabled=True, penalty_rate="12")

    # Sign the contract so we can issue underlying invoices.
    c = (await auth_client.post(f"/api/v1/partners/{p['id']}/documents",
         json={"kind": "contract", "title": "Contract"})).json()

    # Before signing, issuing is blocked → sign first.
    await auth_client.post(f"/api/v1/partners/{p['id']}/documents/{c['id']}/sign", json={})

    # Two overdue invoices (penalty rate inherited from the partner).
    await auth_client.post("/api/v1/issued", json=_invoice(p["id"], "1000.00", issue="2025-12-01", due="2026-01-01"))
    await auth_client.post("/api/v1/issued", json=_invoice(p["id"], "500.00", issue="2025-12-01", due="2026-01-01"))

    pen = (await auth_client.get(f"/api/v1/partners/{p['id']}/penalty")).json()
    assert pen["can_generate"] is True
    assert float(pen["total_penalty"]) > 0
    assert pen["max_days_overdue"] > 0
    assert len(pen["lines"]) == 2

    gen = await auth_client.post(f"/api/v1/partners/{p['id']}/penalty-invoice")
    assert gen.status_code == 201, gen.text
    inv = gen.json()
    assert inv["kind"] == "penalty"
    assert inv["vat_scheme"] == "exempt"
    assert float(inv["total"]) == float(pen["total_penalty"])


@pytest.mark.asyncio
async def test_penalty_invoice_blocked_without_contract(auth_client):
    await _activate(auth_client)
    # Penalty enabled but NO contract requirement/signed → generation blocked.
    p = await _partner(auth_client, penalty_enabled=True, penalty_rate="12")
    await auth_client.post("/api/v1/issued", json=_invoice(p["id"], "1000.00", issue="2025-12-01", due="2026-01-01"))

    pen = (await auth_client.get(f"/api/v1/partners/{p['id']}/penalty")).json()
    assert pen["can_generate"] is False
    assert "contract" in pen["blocked_reason"].lower()

    r = await auth_client.post(f"/api/v1/partners/{p['id']}/penalty-invoice")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_penalty_invoice_blocked_when_disabled(auth_client):
    await _activate(auth_client)
    p = await _partner(auth_client, requires_contract=True, penalty_enabled=False, penalty_rate="12")
    c = (await auth_client.post(f"/api/v1/partners/{p['id']}/documents", json={"kind": "contract", "title": "C"})).json()
    await auth_client.post(f"/api/v1/partners/{p['id']}/documents/{c['id']}/sign", json={})
    await auth_client.post("/api/v1/issued", json=_invoice(p["id"], "1000.00", issue="2025-12-01", due="2026-01-01"))

    pen = (await auth_client.get(f"/api/v1/partners/{p['id']}/penalty")).json()
    assert pen["can_generate"] is False
    assert "not enabled" in pen["blocked_reason"].lower()


@pytest.mark.asyncio
async def test_partners_require_module(auth_client):
    r = await auth_client.get("/api/v1/partners")
    assert r.status_code == 403
