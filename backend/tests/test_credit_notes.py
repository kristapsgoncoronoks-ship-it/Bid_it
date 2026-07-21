"""Credit notes: a linked, negative-turnover correction of an issued invoice."""

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
    "buyer_vat_number": "FR40303265045",
    "issue_date": "2026-05-10",
    "lines": [
        {"description": "Consulting", "quantity": "2", "unit_price": "100.00", "vat_rate": "19"},
        {"description": "Support", "quantity": "1", "unit_price": "50.00", "vat_rate": "7"},
    ],
}


async def _activate(auth_client):
    assert (await auth_client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
    assert (
        await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    ).status_code == 200


async def _make_invoice(auth_client) -> dict:
    r = await auth_client.post("/api/v1/issued", json=INVOICE)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_full_credit_note_cancels_invoice(auth_client):
    await _activate(auth_client)
    inv = await _make_invoice(auth_client)
    assert inv["total"] == "291.50"

    # No custom reason → the note defaults to a reference to the corrected invoice.
    r = await auth_client.post(f"/api/v1/issued/{inv['id']}/credit-note", json={})
    assert r.status_code == 201, r.text
    cn = r.json()
    assert cn["doc_type"] == "credit_note"
    assert cn["number"] == "CN-2026-0001"  # own series
    assert cn["corrected_invoice_id"] == inv["id"]
    assert cn["total"] == "291.50"
    assert cn["status"] == "credit_note"
    assert cn["outstanding"] == "0.00"  # a credit note owes nothing
    assert inv["number"] in cn["note"]  # default note references the invoice

    # A custom reason overrides the note.
    r2 = await auth_client.post("/api/v1/issued", json=INVOICE)
    inv2 = r2.json()
    cn2 = (
        await auth_client.post(
            f"/api/v1/issued/{inv2['id']}/credit-note", json={"reason": "Order cancelled"}
        )
    ).json()
    assert cn2["note"] == "Order cancelled"

    # The corrected invoice is now fully credited → nothing outstanding.
    got = (await auth_client.get(f"/api/v1/issued/{inv['id']}")).json()
    assert got["credited_total"] == "291.50"
    assert got["outstanding"] == "0.00"
    assert got["status"] == "credited"


@pytest.mark.asyncio
async def test_partial_credit_note_reduces_outstanding(auth_client):
    await _activate(auth_client)
    inv = await _make_invoice(auth_client)

    # Credit only the 50.00 support line (+7% = 53.50).
    body = {
        "lines": [
            {
                "description": "Support refund",
                "quantity": "1",
                "unit_price": "50.00",
                "vat_rate": "7",
            }
        ]
    }
    cn = (await auth_client.post(f"/api/v1/issued/{inv['id']}/credit-note", json=body)).json()
    assert cn["total"] == "53.50"

    got = (await auth_client.get(f"/api/v1/issued/{inv['id']}")).json()
    assert got["credited_total"] == "53.50"
    # 291.50 − 53.50 = 238.00 still owed (invoice is past its due date → overdue).
    assert got["outstanding"] == "238.00"
    assert got["status"] == "overdue"


@pytest.mark.asyncio
async def test_cannot_over_credit(auth_client):
    await _activate(auth_client)
    inv = await _make_invoice(auth_client)
    # First full credit succeeds.
    assert (
        await auth_client.post(f"/api/v1/issued/{inv['id']}/credit-note", json={})
    ).status_code == 201
    # A second credit has nothing left to credit.
    r = await auth_client.post(f"/api/v1/issued/{inv['id']}/credit-note", json={})
    assert r.status_code == 400
    assert "already fully credited" in r.json()["detail"]


@pytest.mark.asyncio
async def test_partial_credit_over_remaining_rejected(auth_client):
    await _activate(auth_client)
    inv = await _make_invoice(auth_client)
    # Try to credit more than the invoice total.
    body = {
        "lines": [
            {"description": "Too much", "quantity": "1", "unit_price": "10000.00", "vat_rate": "0"}
        ]
    }
    r = await auth_client.post(f"/api/v1/issued/{inv['id']}/credit-note", json=body)
    assert r.status_code == 400
    assert "exceeds" in r.json()["detail"]


@pytest.mark.asyncio
async def test_cannot_credit_a_credit_note(auth_client):
    await _activate(auth_client)
    inv = await _make_invoice(auth_client)
    cn = (await auth_client.post(f"/api/v1/issued/{inv['id']}/credit-note", json={})).json()
    r = await auth_client.post(f"/api/v1/issued/{cn['id']}/credit-note", json={})
    assert r.status_code == 400
    assert "credit a credit note" in r.json()["detail"]


@pytest.mark.asyncio
async def test_payment_capped_at_credited_total(auth_client):
    await _activate(auth_client)
    inv = await _make_invoice(auth_client)
    # Credit the support line (53.50) → owed becomes 238.00.
    body = {
        "lines": [
            {
                "description": "Support refund",
                "quantity": "1",
                "unit_price": "50.00",
                "vat_rate": "7",
            }
        ]
    }
    await auth_client.post(f"/api/v1/issued/{inv['id']}/credit-note", json=body)

    # Paying the full original 291.50 is now refused.
    over = await auth_client.patch(
        f"/api/v1/issued/{inv['id']}/payment", json={"amount_paid": "291.50"}
    )
    assert over.status_code == 400
    assert "owed" in over.json()["detail"]

    # Paying the effective 238.00 settles it.
    ok = await auth_client.patch(
        f"/api/v1/issued/{inv['id']}/payment", json={"amount_paid": "238.00"}
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "paid"


@pytest.mark.asyncio
async def test_credit_note_reduces_turnover_in_reports(auth_client):
    await _activate(auth_client)
    inv = await _make_invoice(auth_client)
    await auth_client.post(f"/api/v1/issued/{inv['id']}/credit-note", json={})  # full credit

    summ = (await auth_client.get("/api/v1/issued/reports/summary")).json()
    # One invoice fully credited → net turnover nets to zero, count excludes the CN.
    assert summ["count"] == 1
    assert summ["net"] == "0.00"
    assert summ["gross"] == "0.00"
    assert summ["outstanding"] == "0.00"

    # Receivables: the invoice shows as credited, no outstanding.
    recv = (await auth_client.get("/api/v1/issued/reports/receivables")).json()
    assert recv["total_outstanding"] == "0.00"
    by_status = {s["status"]: s for s in recv["statuses"]}
    assert by_status["credited"]["count"] == 1

    # Output VAT nets to zero after the credit note.
    vat = (await auth_client.get("/api/v1/issued/reports/vat")).json()
    assert vat["total_vat"] == "0.00"


@pytest.mark.asyncio
async def test_credit_note_pdf_marked_as_credit_note(auth_client):
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdf")
    from app.services import einvoice

    await _activate(auth_client)
    inv = await _make_invoice(auth_client)
    cn = (await auth_client.post(f"/api/v1/issued/{inv['id']}/credit-note", json={})).json()

    pdf = await auth_client.get(f"/api/v1/issued/{cn['id']}/pdf")
    assert pdf.status_code == 200
    xml = einvoice.extract_embedded_xml(pdf.content)
    assert xml is not None
    # UNTDID 1001 credit-note type code.
    assert b"<ram:TypeCode>381</ram:TypeCode>" in xml
