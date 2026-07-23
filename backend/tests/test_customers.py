"""Sales customer master: CRUD + contacts, soft delete, authorization, and
raising an invoice from a customer (buyer block + terms prefilled)."""

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
CUSTOMER = {
    "name": "Globex SARL",
    "vat_number": "FR40303265045",
    "email": "ap@globex.example",
    "address_line1": "10 Rue de Rivoli",
    "city": "Paris",
    "postal_code": "75001",
    "country": "FR",
    "ship_address_line1": "Warehouse 4, Zone Industrielle",
    "ship_city": "Lyon",
    "payment_terms_days": 30,
    "default_currency": "EUR",
    "contacts": [{"name": "Marie Dubois", "email": "marie@globex.example", "role": "AP"}],
}


def _h(t):
    return {"Authorization": f"Bearer {t}"}


async def _activate(auth_client):
    assert (await auth_client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
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


@pytest.mark.asyncio
async def test_customer_crud_and_contacts(auth_client):
    await _activate(auth_client)
    c = await auth_client.post("/api/v1/customers", json=CUSTOMER)
    assert c.status_code == 201, c.text
    body = c.json()
    cid = body["id"]
    assert body["ship_city"] == "Lyon" and len(body["contacts"]) == 1

    lst = (await auth_client.get("/api/v1/customers")).json()
    assert any(x["id"] == cid for x in lst)

    # Patch a field + replace contacts.
    up = await auth_client.patch(
        f"/api/v1/customers/{cid}",
        json={"phone": "+33 1 23 45 67 89", "contacts": []},
    )
    assert up.status_code == 200 and up.json()["phone"].startswith("+33")
    assert up.json()["contacts"] == []


@pytest.mark.asyncio
async def test_soft_delete_hides_from_default_list(auth_client):
    await _activate(auth_client)
    cid = (await auth_client.post("/api/v1/customers", json=CUSTOMER)).json()["id"]
    assert (await auth_client.delete(f"/api/v1/customers/{cid}")).status_code == 204
    assert all(x["id"] != cid for x in (await auth_client.get("/api/v1/customers")).json())
    # Still visible with include_inactive.
    withall = (await auth_client.get("/api/v1/customers?include_inactive=true")).json()
    assert any(x["id"] == cid and x["is_active"] is False for x in withall)


@pytest.mark.asyncio
async def test_issue_from_customer_prefills_buyer_and_terms(auth_client):
    await _activate(auth_client)
    cid = (await auth_client.post("/api/v1/customers", json=CUSTOMER)).json()["id"]
    r = await auth_client.post(
        "/api/v1/issued",
        json={
            "customer_id": cid,
            "issue_date": "2026-05-01",
            "lines": [
                {"description": "Work", "quantity": "1", "unit_price": "100", "vat_rate": "20"}
            ],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["buyer_name"] == "Globex SARL"
    assert inv["buyer_vat_number"] == "FR40303265045"
    assert inv["due_date"] == "2026-05-31"  # issue + 30d customer terms


@pytest.mark.asyncio
async def test_read_only_user_cannot_manage_customers(auth_client, client):
    await _activate(auth_client)
    # A free/read-only user has ISSUED_READ but not ISSUED_WRITE.
    ro = await _member(auth_client, client, "ro@corp.io", role="user_free")
    listed = await client.get("/api/v1/customers", headers=_h(ro))
    assert listed.status_code == 200  # read allowed
    created = await client.post("/api/v1/customers", headers=_h(ro), json=CUSTOMER)
    assert created.status_code == 403  # write denied
