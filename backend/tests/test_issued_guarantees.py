"""INV-1 critical guarantees for customer-invoice issuing:
- concurrency-safe numbering (sequential + a DB unique-number backstop),
- idempotent email send,
- recurring generation cannot create duplicate occurrences.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.issued_invoice import IssuedInvoice

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
    "buyer_email": "ap@globex.example",
    "buyer_country": "FR",
    "issue_date": "2026-05-10",
    "lines": [
        {"description": "Consulting", "quantity": "2", "unit_price": "100.00", "vat_rate": "19"}
    ],
}


async def _activate(auth_client):
    assert (await auth_client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
    assert (
        await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    ).status_code == 200


async def _issue(auth_client, **over):
    r = await auth_client.post("/api/v1/issued", json={**INVOICE, **over})
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Numbering
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_numbers_are_sequential_and_unique(auth_client):
    await _activate(auth_client)
    numbers = [(await _issue(auth_client))["number"] for _ in range(4)]
    assert numbers == ["INV-2026-0001", "INV-2026-0002", "INV-2026-0003", "INV-2026-0004"]
    assert len(set(numbers)) == 4


@pytest.mark.asyncio
async def test_duplicate_number_is_rejected_by_the_db(auth_client, db_session):
    """The DB backstop: even if two racing transactions produced the same number,
    the unique (org_id, number) constraint refuses the second."""
    await _activate(auth_client)
    inv = await _issue(auth_client)
    orig = await db_session.get(IssuedInvoice, inv["id"])
    dup = IssuedInvoice(
        org_id=orig.org_id,
        doc_type="invoice",
        number=orig.number,  # same number → must violate the unique constraint
        issue_date=orig.issue_date,
        currency=orig.currency,
        buyer_name="Dup",
        seller_json=orig.seller_json,
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_recurring_occurrence_dedup_constraint(auth_client, db_session):
    """Two invoices for the same (schedule, run date) cannot both exist — the
    cross-worker backstop for recurring generation."""
    await _activate(auth_client)
    a = await _issue(auth_client)
    row = await db_session.get(IssuedInvoice, a["id"])
    from datetime import date

    rec_id = "11111111-1111-1111-1111-111111111111"
    row.recurring_id = rec_id
    row.recurring_period = date(2026, 6, 1)
    await db_session.flush()
    dup = IssuedInvoice(
        org_id=row.org_id,
        doc_type="invoice",
        number="INV-2026-9999",  # distinct number, same recurring occurrence
        issue_date=row.issue_date,
        currency=row.currency,
        buyer_name="Dup",
        seller_json=row.seller_json,
        recurring_id=rec_id,
        recurring_period=date(2026, 6, 1),
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


# --------------------------------------------------------------------------- #
# Email idempotency
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_send_is_idempotent(auth_client):
    await _activate(auth_client)
    inv = await _issue(auth_client)
    iid = inv["id"]

    first = await auth_client.post(f"/api/v1/issued/{iid}/send", json={})
    assert first.status_code == 200 and first.json()["already_sent"] is False

    # Repeat send: idempotent no-op — no second email is dispatched.
    again = await auth_client.post(f"/api/v1/issued/{iid}/send", json={})
    assert again.status_code == 200 and again.json()["already_sent"] is True

    emails = (await auth_client.get("/api/v1/issued/emails")).json()
    invoice_sends = [m for m in emails if m["invoice_id"] == iid and m["kind"] == "invoice"]
    assert len(invoice_sends) == 1  # exactly one email, despite two calls

    # An explicit resend DOES dispatch again.
    resent = await auth_client.post(f"/api/v1/issued/{iid}/send", json={"resend": True})
    assert resent.status_code == 200 and resent.json()["already_sent"] is False
    emails2 = (await auth_client.get("/api/v1/issued/emails")).json()
    assert len([m for m in emails2 if m["invoice_id"] == iid and m["kind"] == "invoice"]) == 2
