"""G3.5 (WO-72) — the orphan check (`BA_fleet_fuel.md` §3.J item 3,
verbatim: 'every transaction must be covered by a registered invoice').
Transaction-grain, read-only, persists nothing."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.invoice import Invoice
from app.models.transport.receipt_control import VatReceiptControl
from app.models.vendor import Vendor
from app.services.transport import fuel_ingest, receipt_control
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

PERIOD = "2026-05"


async def _make_txn(db_session, org, entity, *, supplier, invoice_ref, line_seq=1):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier=supplier,
        period=PERIOD,
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 5, 10),
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency="EUR",
        net_local=Decimal("100.00"),
        vat_local=Decimal("21.00"),
        gross_local=Decimal("121.00"),
        net_eur=Decimal("100.00"),
        vat_eur=Decimal("21.00"),
        invoice_ref=invoice_ref,
    )


@pytest.mark.asyncio
async def test_g3_5_covered_transaction_is_not_an_orphan_uncovered_is(db_session):
    """The G3.5 acceptance line, verbatim: 'a transaction with no
    registered invoice surfaces as an orphan.'"""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = Vendor(org_id=org.id, name="BP", country="LV")
    db_session.add(vendor)
    await db_session.flush()
    db_session.add(
        Invoice(
            org_id=org.id,
            vendor_id=vendor.id,
            invoice_number="BP-2026-05-001",
            issue_date=date(2026, 5, 1),
            currency="EUR",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("21.00"),
            total=Decimal("121.00"),
        )
    )
    await db_session.commit()

    covered = await _make_txn(
        db_session, org, entity, supplier="BP", invoice_ref="BP-2026-05-001", line_seq=1
    )
    uncovered = await _make_txn(
        db_session, org, entity, supplier="CASHCO", invoice_ref="RECEIPT-123", line_seq=2
    )
    await db_session.commit()

    orphans = await receipt_control.orphan_transactions(db_session, org.id, PERIOD)
    ids = {o.transaction_id for o in orphans}
    assert uncovered.id in ids
    assert covered.id not in ids
    found = next(o for o in orphans if o.transaction_id == uncovered.id)
    assert found.supplier == "CASHCO"
    assert found.invoice_ref == "RECEIPT-123"


@pytest.mark.asyncio
async def test_g3_5_synthetic_ref_is_always_an_orphan(db_session):
    """A synthetic `INPUT` placeholder can never be covered by anything —
    `resolve_invoice_ref` short-circuits it to None (R3)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity, supplier="CASHCO", invoice_ref="INPUT-CASH")
    await db_session.commit()

    orphans = await receipt_control.orphan_transactions(db_session, org.id, PERIOD)
    assert [o.transaction_id for o in orphans] == [txn.id]


@pytest.mark.asyncio
async def test_g3_5_orphan_check_persists_nothing(db_session):
    """A report function, not a run — §4.19: reads only, writes nothing
    (no control rows, no mutation of the transaction)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    await _make_txn(db_session, org, entity, supplier="E100", invoice_ref=None)
    await db_session.commit()

    orphans = await receipt_control.orphan_transactions(db_session, org.id, PERIOD)
    assert len(orphans) == 1

    count = await db_session.scalar(
        select(func.count())
        .select_from(VatReceiptControl)
        .where(VatReceiptControl.org_id == org.id)
    )
    assert count == 0
    assert not db_session.dirty and not db_session.new
