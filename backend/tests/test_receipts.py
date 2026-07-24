"""AR receipts + allocation (Slice 5c): a received amount is split across issued
invoices; each allocation drives the invoice's amount_paid via the payment
ledger; over-allocation (vs the receipt) and over-payment (vs the invoice) are
refused; the link is tenant-safe at the DB."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.issued_invoice import IssuedInvoice
from app.models.organization import Organization
from app.models.payment import Payment
from app.models.receipt import Receipt

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


async def _invoice(auth_client, price):
    body = {
        "buyer_name": "Acme",
        "issue_date": "2026-01-10",
        "due_date": "2026-02-10",
        "vat_scheme": "standard",
        "lines": [
            {"description": "Service", "quantity": "1", "unit_price": price, "vat_rate": "21"}
        ],
    }
    return (await auth_client.post("/api/v1/issued", json=body)).json()


async def _paid(db_session, inv_id):
    row = await db_session.scalar(select(IssuedInvoice).where(IssuedInvoice.id == inv_id))
    return row.amount_paid


@pytest.mark.asyncio
async def test_receipt_allocates_across_invoices(auth_client, db_session):
    await _activate(auth_client)
    inv1 = await _invoice(auth_client, "1000.00")  # total 1210.00
    inv2 = await _invoice(auth_client, "500.00")  # total 605.00

    rec = (
        await auth_client.post(
            "/api/v1/receipts", json={"amount": "1500.00", "received_on": "2026-02-05"}
        )
    ).json()
    assert rec["unallocated"] == "1500.00"
    rid = rec["id"]

    a1 = await auth_client.post(
        f"/api/v1/receipts/{rid}/allocate", json={"invoice_id": inv1["id"], "amount": "1210.00"}
    )
    assert a1.status_code == 200, a1.text
    assert a1.json()["unallocated"] == "290.00"
    assert await _paid(db_session, inv1["id"]) == Decimal("1210.00")

    a2 = await auth_client.post(
        f"/api/v1/receipts/{rid}/allocate", json={"invoice_id": inv2["id"], "amount": "290.00"}
    )
    detail = a2.json()
    assert detail["unallocated"] == "0.00" and len(detail["allocations"]) == 2
    assert await _paid(db_session, inv2["id"]) == Decimal("290.00")


@pytest.mark.asyncio
async def test_over_allocation_refused(auth_client):
    await _activate(auth_client)
    inv = await _invoice(auth_client, "1000.00")  # 1210
    rec = (
        await auth_client.post(
            "/api/v1/receipts", json={"amount": "300.00", "received_on": "2026-02-05"}
        )
    ).json()
    # Exceeds the receipt's unallocated balance (300).
    r = await auth_client.post(
        f"/api/v1/receipts/{rec['id']}/allocate", json={"invoice_id": inv["id"], "amount": "400.00"}
    )
    assert r.status_code == 400 and "unallocated" in r.json()["detail"]


@pytest.mark.asyncio
async def test_over_invoice_refused(auth_client):
    await _activate(auth_client)
    inv = await _invoice(auth_client, "1000.00")  # outstanding 1210
    rec = (
        await auth_client.post(
            "/api/v1/receipts", json={"amount": "5000.00", "received_on": "2026-02-05"}
        )
    ).json()
    r = await auth_client.post(
        f"/api/v1/receipts/{rec['id']}/allocate",
        json={"invoice_id": inv["id"], "amount": "1300.00"},
    )
    assert r.status_code == 400 and "outstanding" in r.json()["detail"]


@pytest.mark.asyncio
async def test_cross_tenant_receipt_link_blocked_at_db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as db:
            a, b = Organization(name="A"), Organization(name="B")
            db.add_all([a, b])
            await db.flush()
            rec_a = Receipt(org_id=a.id, amount=Decimal("100.00"), received_on=date(2026, 1, 1))
            inv_b = IssuedInvoice(
                org_id=b.id,
                number="B-1",
                issue_date=date(2026, 1, 1),
                buyer_name="x",
                seller_json="{}",
            )
            db.add_all([rec_a, inv_b])
            await db.flush()
            # org B payment pointing at org A's receipt → composite FK violation.
            db.add(
                Payment(
                    org_id=b.id,
                    issued_invoice_id=inv_b.id,
                    amount=Decimal("10.00"),
                    paid_on=date(2026, 1, 2),
                    receipt_id=rec_a.id,
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
    finally:
        await engine.dispose()
