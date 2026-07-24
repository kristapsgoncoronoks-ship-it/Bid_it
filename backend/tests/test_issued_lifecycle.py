"""Issued-invoice lifecycle (Phase 10): void/cancel, delivery (sent_at), and the
guards that a voided invoice refuses payment / credit-note / send. Plus that the
ISSUED_* permissions are now actually enforced on the issued routes."""

import pytest

ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "billing@invoiceiq.test",
}
INVOICE = {
    "buyer_name": "Globex SARL",
    "buyer_country": "FR",
    "issue_date": "2026-05-10",
    "lines": [
        {"description": "Consulting", "quantity": "1", "unit_price": "100.00", "vat_rate": "0"}
    ],
}


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _activate(auth_client):
    assert (await auth_client.put("/api/v1/issuer", json=ISSUER)).json()["is_complete"] is True
    assert (
        await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    ).status_code == 200


async def _member(auth_client, client, email, role="user"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "M", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


async def _issue(auth_client):
    r = await auth_client.post("/api/v1/issued", json=INVOICE)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_void_open_invoice_then_guards(auth_client):
    await _activate(auth_client)
    iid = await _issue(auth_client)
    v = await auth_client.post(f"/api/v1/issued/{iid}/void", json={"reason": "duplicate"})
    assert v.status_code == 200, v.text
    assert v.json()["status"] == "void" and v.json()["voided_at"] is not None

    # A voided invoice refuses payment, credit-note, send, and a second void.
    assert (
        await auth_client.patch(f"/api/v1/issued/{iid}/payment", json={"amount_paid": "100.00"})
    ).status_code == 409
    assert (await auth_client.post(f"/api/v1/issued/{iid}/credit-note", json={})).status_code == 409
    assert (
        await auth_client.post(f"/api/v1/issued/{iid}/send", json={"to_email": "b@x.io"})
    ).status_code == 409
    assert (await auth_client.post(f"/api/v1/issued/{iid}/void", json={})).status_code == 409


@pytest.mark.asyncio
async def test_cannot_void_paid_invoice(auth_client):
    await _activate(auth_client)
    iid = await _issue(auth_client)
    pay = await auth_client.patch(f"/api/v1/issued/{iid}/payment", json={"amount_paid": "100.00"})
    assert pay.status_code == 200 and pay.json()["status"] == "paid"
    v = await auth_client.post(f"/api/v1/issued/{iid}/void", json={})
    assert v.status_code == 409 and "payments" in v.json()["detail"]


@pytest.mark.asyncio
async def test_cannot_void_credited_invoice(auth_client):
    await _activate(auth_client)
    iid = await _issue(auth_client)
    assert (await auth_client.post(f"/api/v1/issued/{iid}/credit-note", json={})).status_code == 201
    v = await auth_client.post(f"/api/v1/issued/{iid}/void", json={})
    assert v.status_code == 409 and "credit notes" in v.json()["detail"]


@pytest.mark.asyncio
async def test_send_records_sent_at(auth_client):
    await _activate(auth_client)
    iid = await _issue(auth_client)
    assert (await auth_client.get(f"/api/v1/issued/{iid}")).json()["sent_at"] is None
    sent = await auth_client.post(f"/api/v1/issued/{iid}/send", json={"to_email": "buyer@x.io"})
    assert sent.status_code == 200, sent.text
    assert (await auth_client.get(f"/api/v1/issued/{iid}")).json()["sent_at"] is not None


@pytest.mark.asyncio
async def test_issued_permission_enforced(auth_client, client):
    await _activate(auth_client)
    await _issue(auth_client)
    # An employee (role 'user' → EMPLOYEE) has no ISSUED_READ → the router gate 403s,
    # even though the issuing module is enabled for the org.
    emp = await _member(auth_client, client, "emp@corp.io", role="user")
    assert (await client.get("/api/v1/issued", headers=_h(emp))).status_code == 403
    # An admin (→ ADMINISTRATOR) can read.
    adm = await _member(auth_client, client, "adm@corp.io", role="admin")
    assert (await client.get("/api/v1/issued", headers=_h(adm))).status_code == 200
