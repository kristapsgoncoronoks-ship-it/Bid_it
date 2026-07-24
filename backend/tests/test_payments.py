"""AR payment ledger (Slice 3c): the ledger records every change to an issued
invoice's cumulative amount_paid, keeps that cache consistent, exposes history,
backfills the pre-ledger settlements, and is tenant-safe at the DB."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.issued_invoice import IssuedInvoice
from app.models.organization import Organization
from app.models.payment import Payment
from app.services import payments

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


async def _new_invoice(auth_client, price="1000.00"):
    """Create an issued invoice; 21% VAT → total = price * 1.21."""
    body = {
        "buyer_name": "Acme",
        "issue_date": "2026-01-10",
        "due_date": "2026-02-10",
        "vat_scheme": "standard",
        "lines": [
            {"description": "Service", "quantity": "1", "unit_price": price, "vat_rate": "21"}
        ],
    }
    r = await auth_client.post("/api/v1/issued", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _pay(auth_client, inv_id, amount, paid_date=None):
    body = {"amount_paid": amount}
    if paid_date:
        body["paid_date"] = paid_date
    return await auth_client.patch(f"/api/v1/issued/{inv_id}/payment", json=body)


# --- ledger + history ------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_records_ledger_entry_and_history(auth_client):
    await _activate(auth_client)
    inv = await _new_invoice(auth_client)  # total 1210.00
    r = await _pay(auth_client, inv["id"], "1210.00", "2026-02-05")
    assert r.status_code == 200 and r.json()["status"] == "paid"

    hist = (await auth_client.get(f"/api/v1/issued/{inv['id']}/payments")).json()
    assert len(hist) == 1
    assert hist[0]["amount"] == "1210.00" and hist[0]["method"] == "bank_transfer"
    assert hist[0]["paid_on"] == "2026-02-05"


@pytest.mark.asyncio
async def test_cumulative_updates_record_signed_deltas(auth_client, db_session):
    await _activate(auth_client)
    inv = await _new_invoice(auth_client)  # 1210.00
    await _pay(auth_client, inv["id"], "500.00")  # +500
    await _pay(auth_client, inv["id"], "1210.00")  # +710 (now fully paid)
    await _pay(auth_client, inv["id"], "800.00")  # -410 correction

    hist = (await auth_client.get(f"/api/v1/issued/{inv['id']}/payments")).json()
    amounts = [Decimal(h["amount"]) for h in hist]
    assert amounts == [Decimal("500.00"), Decimal("710.00"), Decimal("-410.00")]

    # The cache equals the ledger sum.
    row = await db_session.scalar(select(IssuedInvoice).where(IssuedInvoice.id == inv["id"]))
    assert row.amount_paid == Decimal("800.00") == sum(amounts)


@pytest.mark.asyncio
async def test_overpay_still_refused(auth_client):
    await _activate(auth_client)
    inv = await _new_invoice(auth_client)  # 1210.00
    r = await _pay(auth_client, inv["id"], "99999.00")
    assert r.status_code == 400
    hist = (await auth_client.get(f"/api/v1/issued/{inv['id']}/payments")).json()
    assert hist == []  # nothing recorded on a rejected overpay


@pytest.mark.asyncio
async def test_unchanged_total_records_nothing(auth_client):
    await _activate(auth_client)
    inv = await _new_invoice(auth_client)
    await _pay(auth_client, inv["id"], "600.00")
    await _pay(auth_client, inv["id"], "600.00")  # same total → no ledger entry
    hist = (await auth_client.get(f"/api/v1/issued/{inv['id']}/payments")).json()
    assert len(hist) == 1


# --- backfill --------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_ledger_idempotent(auth_client, db_session):
    await _activate(auth_client)
    inv = await _new_invoice(auth_client)
    org_id = await db_session.scalar(select(Organization.id))
    # Simulate a pre-ledger settlement written straight to the cache.
    row = await db_session.scalar(select(IssuedInvoice).where(IssuedInvoice.id == inv["id"]))
    row.amount_paid = Decimal("1210.00")
    row.paid_date = date(2026, 2, 5)
    await db_session.commit()

    assert await payments.backfill_ledger(db_session, org_id) == 1
    assert await payments.backfill_ledger(db_session, org_id) == 0  # idempotent
    n = await db_session.scalar(
        select(func.count(Payment.id)).where(Payment.issued_invoice_id == inv["id"])
    )
    assert n == 1


# --- tenant safety at the DB ----------------------------------------------


@pytest.mark.asyncio
async def test_cross_tenant_payment_blocked_at_db():
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
            inv_a = IssuedInvoice(
                org_id=a.id,
                number="A-1",
                issue_date=date(2026, 1, 1),
                buyer_name="Buyer",
                seller_json="{}",
            )
            db.add(inv_a)
            await db.flush()
            # org B payment pointing at org A's invoice → composite FK violation.
            db.add(
                Payment(
                    org_id=b.id,
                    issued_invoice_id=inv_a.id,
                    amount=Decimal("10.00"),
                    paid_on=date(2026, 1, 2),
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
    finally:
        await engine.dispose()
