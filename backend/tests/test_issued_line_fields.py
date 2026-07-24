"""Issued-invoice line/document fields (INV-4): per-line discount, PO reference,
tax-exemption reason (+ reverse-charge default), and supporting attachments."""

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


async def _activate(auth_client):
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})


@pytest.mark.asyncio
async def test_line_discount_reduces_net_and_vat(auth_client):
    await _activate(auth_client)
    payload = {
        "buyer_name": "Globex",
        "issue_date": "2026-05-10",
        "lines": [
            # 10 x 100 = 1000, less 10% = 900 net; VAT 21% = 189; total 1089.
            {
                "description": "Widgets",
                "quantity": "10",
                "unit_price": "100.00",
                "discount_percent": "10",
                "vat_rate": "21",
            }
        ],
    }
    inv = (await auth_client.post("/api/v1/issued", json=payload)).json()
    assert inv["subtotal"] == "900.00"
    assert inv["tax_total"] == "189.00"
    assert inv["total"] == "1089.00"
    assert inv["lines"][0]["discount_percent"] == "10.00"
    assert inv["lines"][0]["net_amount"] == "900.00"


@pytest.mark.asyncio
async def test_discount_out_of_range_rejected(auth_client):
    await _activate(auth_client)
    bad = await auth_client.post(
        "/api/v1/issued",
        json={
            "buyer_name": "X",
            "lines": [
                {
                    "description": "a",
                    "quantity": "1",
                    "unit_price": "10",
                    "discount_percent": "150",
                    "vat_rate": "0",
                }
            ],
        },
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_po_reference_and_exemption_reason_stored_and_in_xml(auth_client):
    await _activate(auth_client)
    payload = {
        "buyer_name": "Globex SARL",
        "buyer_vat_number": "FR40303265045",
        "buyer_country": "FR",
        "issue_date": "2026-05-10",
        "vat_scheme": "reverse_charge",
        "po_reference": "PO-98765",
        "lines": [
            {"description": "Service", "quantity": "1", "unit_price": "250.00", "vat_rate": "0"}
        ],
    }
    inv = (await auth_client.post("/api/v1/issued", json=payload)).json()
    assert inv["po_reference"] == "PO-98765"
    # Reverse-charge → no VAT, and an exemption reason defaults from the scheme.
    assert inv["tax_total"] == "0.00"
    assert inv["tax_exemption_reason"] and "Reverse charge" in inv["tax_exemption_reason"]

    x = await auth_client.get(f"/api/v1/issued/{inv['id']}/xml")
    assert x.status_code == 200
    assert b"PO-98765" in x.content  # BT-13 buyer order reference
    assert b"ExemptionReason" in x.content  # BT-120


@pytest.mark.asyncio
async def test_custom_exemption_reason_overrides_default(auth_client):
    await _activate(auth_client)
    payload = {
        "buyer_name": "Zeta",
        "vat_scheme": "exempt",
        "tax_exemption_reason": "Small-business scheme, Art. 287",
        "lines": [{"description": "x", "quantity": "1", "unit_price": "100", "vat_rate": "0"}],
    }
    inv = (await auth_client.post("/api/v1/issued", json=payload)).json()
    assert inv["tax_exemption_reason"] == "Small-business scheme, Art. 287"


@pytest.mark.asyncio
async def test_discounted_pdf_roundtrips_and_balances(auth_client):
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdf")
    from decimal import Decimal

    from app.services import einvoice

    await _activate(auth_client)
    payload = {
        "buyer_name": "Globex",
        "issue_date": "2026-05-10",
        "lines": [
            {
                "description": "Widgets",
                "quantity": "4",
                "unit_price": "25.00",
                "discount_percent": "20",
                "vat_rate": "21",
            }
        ],
    }
    inv = (await auth_client.post("/api/v1/issued", json=payload)).json()
    # 4 x 25 = 100, less 20% = 80 net.
    assert inv["subtotal"] == "80.00"

    pdf = await auth_client.get(f"/api/v1/issued/{inv['id']}/pdf")
    assert pdf.status_code == 200
    xml = einvoice.extract_embedded_xml(pdf.content)
    draft = einvoice.parse_xml_bytes(xml, "rt.xml").draft
    # The embedded line net reflects the discount (net price × qty = 80).
    assert sum((li.amount for li in draft.line_items), start=Decimal("0")) == Decimal("80.00")


@pytest.mark.asyncio
async def test_attachments_upload_list_download_delete(auth_client):
    await _activate(auth_client)
    inv = (
        await auth_client.post(
            "/api/v1/issued",
            json={
                "buyer_name": "Globex",
                "lines": [
                    {"description": "x", "quantity": "1", "unit_price": "10", "vat_rate": "0"}
                ],
            },
        )
    ).json()
    iid = inv["id"]

    up = await auth_client.post(
        f"/api/v1/issued/{iid}/attachments",
        files={"file": ("po.txt", b"signed purchase order", "text/plain")},
        params={"note": "signed PO"},
    )
    assert up.status_code == 201, up.text
    aid = up.json()["id"]
    assert up.json()["filename"] == "po.txt"
    assert up.json()["note"] == "signed PO"

    lst = await auth_client.get(f"/api/v1/issued/{iid}/attachments")
    assert [a["id"] for a in lst.json()] == [aid]

    dl = await auth_client.get(f"/api/v1/issued/{iid}/attachments/{aid}/download")
    assert dl.status_code == 200
    assert dl.content == b"signed purchase order"
    assert "attachment" in dl.headers["content-disposition"]

    rm = await auth_client.delete(f"/api/v1/issued/{iid}/attachments/{aid}")
    assert rm.status_code == 204
    assert (await auth_client.get(f"/api/v1/issued/{iid}/attachments")).json() == []


@pytest.mark.asyncio
async def test_attachment_rejects_active_content(auth_client):
    await _activate(auth_client)
    inv = (
        await auth_client.post(
            "/api/v1/issued",
            json={
                "buyer_name": "Globex",
                "lines": [
                    {"description": "x", "quantity": "1", "unit_price": "10", "vat_rate": "0"}
                ],
            },
        )
    ).json()
    # A Windows PE executable header is rejected by the filesec gate.
    ex = await auth_client.post(
        f"/api/v1/issued/{inv['id']}/attachments",
        files={"file": ("evil.exe", b"MZ\x90\x00" + b"\x00" * 64, "application/octet-stream")},
    )
    assert ex.status_code == 415


@pytest.mark.asyncio
async def test_attachment_upload_requires_write(auth_client, client):
    await _activate(auth_client)
    inv = (
        await auth_client.post(
            "/api/v1/issued",
            json={
                "buyer_name": "Globex",
                "lines": [
                    {"description": "x", "quantity": "1", "unit_price": "10", "vat_rate": "0"}
                ],
            },
        )
    ).json()
    invite = await auth_client.post(
        "/api/v1/team/invites", json={"email": "ro@acme.io", "role": "user"}
    )
    tok = (
        await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": invite.json()["token"], "name": "RO", "password": "supersecret"},
        )
    ).json()["token"]["access_token"]
    r = await client.post(
        f"/api/v1/issued/{inv['id']}/attachments",
        files={"file": ("po.txt", b"hi", "text/plain")},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 403
