"""Regression tests for the architecture-audit fixes.

- H1: received-invoice CRUD now enforces INVOICE_WRITE/DELETE/APPROVE (a
  read-only member can no longer create/delete/validate supplier invoices).
- H2/H3: `*Update` schemas cap string lengths so an over-length value is a 422
  (Pydantic) instead of a Postgres-only `value too long` 500.
- M2: issued-report CSV export neutralises formula injection.
"""

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


def _invoice_payload(number="INV-1"):
    return {
        "vendor_name": "AWS",
        "invoice_number": number,
        "issue_date": "2026-01-15",
        "currency": "EUR",
        "status": "pending",
        "line_items": [
            {
                "description": "Compute",
                "category": "cloud",
                "quantity": "1",
                "unit_price": "100.00",
                "tax_rate": "21",
            }
        ],
    }


async def _member_token(auth_client, client, email, role="user"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "M", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


# --- H1: received-invoice authorization -----------------------------------------


@pytest.mark.asyncio
async def test_readonly_member_cannot_write_delete_or_validate_invoices(auth_client, client):
    # Owner creates an invoice; a read-only "user" (EMPLOYEE = INVOICE_READ only)
    # may view it but not mutate it.
    inv = (await auth_client.post("/api/v1/invoices", json=_invoice_payload())).json()
    iid = inv["id"]
    tok = await _member_token(auth_client, client, "ro@acme.io", role="user")
    h = {"Authorization": f"Bearer {tok}"}

    assert (await client.get(f"/api/v1/invoices/{iid}", headers=h)).status_code == 200  # read OK
    assert (
        await client.post("/api/v1/invoices", json=_invoice_payload("INV-2"), headers=h)
    ).status_code == 403
    assert (
        await client.patch(f"/api/v1/invoices/{iid}", json={"notes": "x"}, headers=h)
    ).status_code == 403
    assert (
        await client.post(f"/api/v1/invoices/{iid}/validate", json={"action": "approve"}, headers=h)
    ).status_code == 403
    assert (await client.delete(f"/api/v1/invoices/{iid}", headers=h)).status_code == 403


# --- H2 / H3: over-length values are a clean 422, not a Postgres 500 -------------


async def _activate_issuing(auth_client):
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})


@pytest.mark.asyncio
async def test_customer_update_rejects_overlong_country(auth_client):
    await _activate_issuing(auth_client)
    cid = (
        await auth_client.post("/api/v1/customers", json={"name": "Globex", "country": "FR"})
    ).json()["id"]
    # country column is String(2); an uncapped schema would 500 only on Postgres.
    r = await auth_client.patch(f"/api/v1/customers/{cid}", json={"country": "United Kingdom"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_review_header_update_rejects_overlong_currency(auth_client):
    inv = (await auth_client.post("/api/v1/invoices", json=_invoice_payload())).json()
    # currency column is String(3); "EURO" (4) must be rejected at the schema layer.
    r = await auth_client.patch(
        f"/api/v1/invoices/{inv['id']}/review", json={"version": 0, "currency": "EURO"}
    )
    assert r.status_code == 422


# --- M2: CSV formula-injection neutralised --------------------------------------


@pytest.mark.asyncio
async def test_issued_partner_csv_neutralises_formula_injection(auth_client):
    await _activate_issuing(auth_client)
    # A buyer name crafted as a spreadsheet formula.
    await auth_client.post(
        "/api/v1/issued",
        json={
            "buyer_name": "=cmd|'/c calc'!A1",
            "issue_date": "2026-01-10",
            "lines": [{"description": "x", "quantity": "1", "unit_price": "10", "vat_rate": "0"}],
        },
    )
    csv = await auth_client.get("/api/v1/issued/reports/partners?format=csv")
    assert csv.status_code == 200
    body = csv.text
    # The dangerous leading '=' is prefixed with a quote so Excel won't evaluate it.
    assert "'=cmd" in body
    assert "\n=cmd" not in body and not body.startswith("=cmd")
