"""Retention routes invoices through the deletion chain (P0-2, decided 2026-08-16).

`retention.purge` used to hard-delete invoices directly — no recycle bin, no
consent trail, no archive copy — a second destruction path with strictly weaker
guarantees than the one clients see. The owner chose ONE pipeline: the policy
soft-deletes into the bin, and the ordinary chain (30 days restorable → archived
→ three years → expiry) takes over. These tests pin that routing, and the two
edges that made the old design a defect: rows already binned are left to the
bin's own clock, and a racing manual delete is never clobbered.

The archive deliberately outlives a SHORTER tenant policy (owner decision, same
date): it is the platform's compliance backstop, stated in the DPA. The final
test documents that consequence on purpose, so a future reader finds a decision
here rather than an oversight.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core import tenant
from app.models.archived_invoice import ArchivedInvoice
from app.models.invoice import Invoice, LineItem
from app.models.organization import Organization
from app.models.vendor import Vendor
from app.services import invoices as invoice_service
from app.services import retention

OLD = datetime(2025, 1, 1, tzinfo=UTC)  # far past any window a test configures


async def _org_id(db) -> str:
    return await db.scalar(select(Organization.id))


async def _invoice(db, org_id, number, *, created_at=OLD, lines=0):
    vendor = await db.scalar(select(Vendor).where(Vendor.org_id == org_id))
    if vendor is None:
        vendor = Vendor(org_id=org_id, name="Fictional Fuels OU")
        db.add(vendor)
        await db.flush()
    inv = Invoice(
        org_id=org_id,
        vendor_id=vendor.id,
        invoice_number=number,
        issue_date=date(2025, 1, 1),
        created_at=created_at,
    )
    db.add(inv)
    await db.flush()
    for i in range(lines):
        db.add(LineItem(invoice_id=inv.id, description=f"Diesel {i}", amount=100))
    return inv.id


@pytest.mark.asyncio
async def test_retention_bins_an_old_invoice_instead_of_destroying_it(auth_client, db_session):
    org_id = await _org_id(db_session)
    invoice_id = await _invoice(db_session, org_id, "RET-1", lines=2)
    await retention.set_policy(db_session, org_id, "invoices", 30)

    result = await retention.purge(db_session, org_id)

    assert result["purged"]["invoices"] == 1
    # Hidden from every ordinary read — it is deleted as far as the books know.
    assert await db_session.scalar(select(Invoice.id).where(Invoice.id == invoice_id)) is None
    # But the ROW survives, in the bin, with the policy named as the deleter —
    # a Trash-screen reader must be able to tell a colleague's click from the
    # org's own configured policy before deciding to restore.
    with tenant.include_deleted():
        row = await db_session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    assert row is not None
    assert row.deleted_at is not None
    assert row.deleted_by == retention.RETENTION_ACTOR
    # Line items survive with it: a binned invoice must be restorable WHOLE.
    lines = list(
        await db_session.scalars(select(LineItem).where(LineItem.invoice_id == invoice_id))
    )
    assert len(lines) == 2


@pytest.mark.asyncio
async def test_a_recent_invoice_is_left_alone(auth_client, db_session):
    org_id = await _org_id(db_session)
    invoice_id = await _invoice(
        db_session, org_id, "RET-NEW", created_at=datetime.now(UTC) - timedelta(days=5)
    )
    await retention.set_policy(db_session, org_id, "invoices", 30)

    await retention.purge(db_session, org_id)

    assert await db_session.scalar(select(Invoice.id).where(Invoice.id == invoice_id)) is not None


@pytest.mark.asyncio
async def test_an_invoice_already_in_the_bin_keeps_its_original_deleter(auth_client, db_session):
    """Two protections in one: the bin's own clock is respected (no re-stamping
    that would extend the 30 days), and who/when the bin already recorded is
    never overwritten — the `deleted_at IS NULL` guard on the UPDATE itself."""
    org_id = await _org_id(db_session)
    invoice_id = await _invoice(db_session, org_id, "RET-BINNED")
    manual_stamp = datetime.now(UTC) - timedelta(days=10)
    with tenant.include_deleted():
        row = await db_session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    row.deleted_at = manual_stamp
    row.deleted_by = "someone@acme.io"
    await db_session.commit()
    await retention.set_policy(db_session, org_id, "invoices", 30)

    result = await retention.purge(db_session, org_id)

    # Not counted — the guard hid it from the selection entirely.
    assert result["purged"].get("invoices") is None
    with tenant.include_deleted():
        row = await db_session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    assert row.deleted_by == "someone@acme.io"
    assert abs((row.deleted_at - manual_stamp).total_seconds()) < 1


@pytest.mark.asyncio
async def test_the_full_chain_a_policy_binned_invoice_reaches_the_archive(auth_client, db_session):
    """The property the rerouting buys: the archive copy of a policy-purged
    invoice is made by the SAME code path as every other deletion — the bin
    purge — so the two can never drift apart. Retention bins it; 31 days later
    the bin purge destroys the row and the archive holds the record."""
    org_id = await _org_id(db_session)
    invoice_id = await _invoice(db_session, org_id, "RET-CHAIN", lines=1)
    await retention.set_policy(db_session, org_id, "invoices", 30)

    await retention.purge(db_session, org_id)
    later = datetime.now(UTC) + timedelta(days=31)
    result = await invoice_service.purge_expired_bin(db_session, org_id, now=later)
    await db_session.commit()

    assert result["purged"] == 1
    archived = await db_session.scalar(
        select(ArchivedInvoice).where(ArchivedInvoice.original_invoice_id == invoice_id)
    )
    assert archived is not None, "a policy-purged invoice must land in the archive"
    assert archived.invoice_number == "RET-CHAIN"
    assert archived.original_deleted_by == retention.RETENTION_ACTOR
    # The archive's OWN window runs from here — deliberately regardless of the
    # tenant's shorter policy (owner decision 2026-08-16; DPA states it).
    # Compared as a duration: SQLite hands back naive datetimes.
    assert (archived.expires_at - archived.archived_at).days >= 3 * 364
    with tenant.include_deleted():
        assert (await db_session.scalar(select(Invoice.id).where(Invoice.id == invoice_id))) is None


@pytest.mark.asyncio
async def test_a_legal_hold_still_stops_the_invoice_binning(auth_client, db_session):
    """The hold check runs before the category loop, so the new branch inherits
    it — pinned anyway, because 'inherited by code position' is exactly the kind
    of property a refactor loses without noticing."""
    org_id = await _org_id(db_session)
    invoice_id = await _invoice(db_session, org_id, "RET-HELD")
    await retention.set_policy(db_session, org_id, "invoices", 30)
    await retention.place_hold(db_session, org_id, reason="Audit", actor_email="legal@t.io")

    result = await retention.purge(db_session, org_id)

    assert result == {"held": True, "purged": {}}
    assert await db_session.scalar(select(Invoice.id).where(Invoice.id == invoice_id)) is not None
