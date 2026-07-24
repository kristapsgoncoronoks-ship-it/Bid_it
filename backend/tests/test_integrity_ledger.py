"""AR-ledger integrity check (Slice 5e): verifies amount_paid == SUM(payments)
per invoice and no receipt over-allocation, over the existing /integrity feature.
Catches drift a document-hash check can't see."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.issued_invoice import IssuedInvoice
from app.models.payment import Payment

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


async def _invoice(auth_client, price="1000.00"):
    body = {
        "buyer_name": "Acme",
        "issue_date": "2026-01-10",
        "due_date": "2026-02-10",
        "vat_scheme": "standard",
        "lines": [{"description": "S", "quantity": "1", "unit_price": price, "vat_rate": "21"}],
    }
    return (await auth_client.post("/api/v1/issued", json=body)).json()


async def _verify(auth_client):
    return (await auth_client.post("/api/v1/integrity/ledger/verify")).json()


@pytest.mark.asyncio
async def test_healthy_then_drift_detected(auth_client, db_session):
    await _activate(auth_client)
    inv = await _invoice(auth_client)  # total 1210
    rec = (
        await auth_client.post(
            "/api/v1/receipts", json={"amount": "1210.00", "received_on": "2026-02-05"}
        )
    ).json()
    await auth_client.post(
        f"/api/v1/receipts/{rec['id']}/allocate",
        json={"invoice_id": inv["id"], "amount": "1210.00"},
    )

    healthy = await _verify(auth_client)
    assert healthy["healthy"] and healthy["checked"] >= 2 and healthy["issues"] == []

    # Corrupt the cache directly (bypassing the ledger) → the check must catch it.
    row = await db_session.scalar(select(IssuedInvoice).where(IssuedInvoice.id == inv["id"]))
    row.amount_paid = Decimal("999.00")
    await db_session.commit()

    broken = await _verify(auth_client)
    assert not broken["healthy"]
    assert any(
        i["kind"] == "invoice_ledger" and i["problem"] == "mismatch" for i in broken["issues"]
    )


@pytest.mark.asyncio
async def test_over_allocation_detected(auth_client, db_session):
    await _activate(auth_client)
    inv = await _invoice(auth_client)
    rec = (
        await auth_client.post(
            "/api/v1/receipts", json={"amount": "100.00", "received_on": "2026-02-05"}
        )
    ).json()
    org_id = await db_session.scalar(select(IssuedInvoice.org_id))
    # Inject an allocation exceeding the receipt (bypassing the capped service).
    db_session.add(
        Payment(
            org_id=org_id,
            issued_invoice_id=inv["id"],
            amount=Decimal("150.00"),
            paid_on=date(2026, 2, 5),
            receipt_id=rec["id"],
        )
    )
    await db_session.commit()

    report = await _verify(auth_client)
    assert any(
        i["kind"] == "receipt_allocation" and i["problem"] == "over_allocated"
        for i in report["issues"]
    )


@pytest.mark.asyncio
async def test_ledger_job_runs(auth_client, db_session):
    from app.services import job_handlers, jobs

    await _activate(auth_client)
    await _invoice(auth_client)
    org_id = await db_session.scalar(select(IssuedInvoice.org_id))
    await jobs.enqueue(db_session, job_handlers.INTEGRITY_LEDGER, org_id=org_id)
    job = await jobs.run_once(db_session, "w")
    assert job is not None and job.status == "succeeded"

    import json

    assert json.loads(job.result_json)["healthy"] is True
