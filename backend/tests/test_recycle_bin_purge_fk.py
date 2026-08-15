"""The purge must survive an invoice the transport vertical references.

`vat_claim_lines` and `fuel_transactions` link to invoices through a COMPOSITE
foreign key `(org_id, invoice_id) -> invoices(org_id, id)` declared
`ON DELETE SET NULL`. SET NULL on a multi-column FK nulls EVERY referencing
column — `org_id` included — and `org_id` is NOT NULL on both tables, so the
database raises instead of nulling:

    NOT NULL constraint failed: fuel_transactions.org_id

Left unhandled, the daily purge raises for any tenant using the transport
module, retries, dead-letters, and repeats tomorrow — so that tenant's bin is
never emptied and its records are invisible AND immortal, the precise failure the
purge exists to prevent. `retention.purge` has the same defect, but only ran for
tenants with a configured policy; putting BIN_PURGE in DAILY_KINDS made it
universal and daily.

Rows are inserted with Core statements rather than the ORM on purpose: this test
is about a DATABASE constraint, and building the transport object graph
(issuer profile, entity, period, natural key) would test the fixture rather than
the constraint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select

from app.core import tenant
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.transport.fuel_transaction import FuelTransaction
from app.services import invoices as invoice_service


async def _org(db_session) -> str:
    return await db_session.scalar(select(Organization.id).where(Organization.name == "Acme"))


async def _binned_invoice(auth_client, db_session, number: str, *, days_ago: int) -> str:
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Fictional Fuels OU",
            "invoice_number": number,
            "issue_date": "2026-06-01",
            "currency": "EUR",
            "line_items": [
                {
                    "description": "Diesel",
                    "quantity": "1",
                    "unit_price": "100.00",
                    "amount": "100.00",
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    invoice_id = r.json()["id"]
    assert (await auth_client.delete(f"/api/v1/invoices/{invoice_id}")).status_code == 204
    with tenant.include_deleted():
        inv = await db_session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    inv.deleted_at = datetime.now(UTC) - timedelta(days=days_ago)
    await db_session.commit()
    return invoice_id


@pytest.mark.asyncio
async def test_the_purge_survives_an_invoice_a_fuel_transaction_points_at(auth_client, db_session):
    org_id = await _org(db_session)
    invoice_id = await _binned_invoice(auth_client, db_session, "INV-FK-1", days_ago=31)

    await db_session.execute(
        insert(FuelTransaction).values(
            org_id=org_id,
            entity_id="00000000-0000-0000-0000-000000000001",
            invoice_id=invoice_id,
            supplier="EUROWAG",
            period="2026-06",
            line_seq=1,
            country="LV",
            vehicle_ref="TRK-1",
            txn_date=datetime.now(UTC).date(),
            station="Riga",
            product="Diesel",
            product_group="Diesel",
            qty=100,
            currency="EUR",
            net_local=100,
            vat_local=21,
            gross_local=121,
            net_eur=100,
            vat_eur=21,
            net_eur_eff=100,
            invoice_ref="INV-FK-1",
        )
    )
    await db_session.commit()

    # Before the fix this raises IntegrityError on fuel_transactions.org_id.
    result = await invoice_service.purge_expired_bin(db_session, org_id)
    await db_session.commit()

    assert result["purged"] == 1
    with tenant.include_deleted():
        assert (await db_session.scalar(select(Invoice.id).where(Invoice.id == invoice_id))) is None

    # The referencing row SURVIVES with its tenancy intact. A fuel transaction is
    # analytics history the client never asked to delete, and a purge that nulled
    # its org_id would corrupt the row rather than unlink it.
    txn = await db_session.scalar(select(FuelTransaction).where(FuelTransaction.org_id == org_id))
    assert txn is not None, "the purge destroyed the fuel transaction"
    assert txn.org_id == org_id, "the purge nulled the referencing row's tenancy"
    assert txn.invoice_id is None, "the link to the destroyed invoice was left dangling"


@pytest.mark.asyncio
async def test_a_batch_larger_than_one_purge_chunk_completes(auth_client, db_session):
    """The bind-parameter ceiling. An unbounded `id.in_(...)` fails above 32766
    binds on SQLite and 65535 on Postgres; the job then fails every day forever
    and the bin never empties. Uses a deliberately tiny batch size so the loop is
    exercised without creating tens of thousands of rows."""
    org_id = await _org(db_session)
    ids = [
        await _binned_invoice(auth_client, db_session, f"INV-BATCH-{i}", days_ago=31)
        for i in range(5)
    ]

    original = invoice_service.PURGE_BATCH
    invoice_service.PURGE_BATCH = 2  # forces three passes
    try:
        result = await invoice_service.purge_expired_bin(db_session, org_id)
        await db_session.commit()
    finally:
        invoice_service.PURGE_BATCH = original

    assert result["purged"] == 5, "the batching loop dropped rows"
    assert len(result["records"]) == 5, "the audit record lost rows across batches"
    with tenant.include_deleted():
        left = list(await db_session.scalars(select(Invoice.id).where(Invoice.id.in_(ids))))
    assert left == []
