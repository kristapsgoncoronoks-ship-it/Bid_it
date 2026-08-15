"""The platform archive — the backstop under the one destructive path.

Owner decisions this encodes (docs/design/platform-archive.md):
  - the purge keeps running, so the archive is the ONLY thing between a client
    and permanent loss;
  - it keeps the record AND the source document;
  - the client's own company owner can read it, read-only;
  - three years included, longer paid;
  - nothing leaves without the owner being told first.

The load-bearing claim is the first test: a record cannot be destroyed without
being archived, because both happen in one transaction. Everything else in this
file is worth less than that one property.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core import tenant
from app.models.archived_invoice import ArchivedInvoice
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.services import archive
from app.services import invoices as invoice_service


async def _org(db_session) -> str:
    return await db_session.scalar(select(Organization.id).where(Organization.name == "Acme"))


async def _binned(client, db_session, number: str, *, days_ago: int, total: str = "1240.50") -> str:
    r = await client.post(
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
                    "unit_price": total,
                    "amount": total,
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    invoice_id = r.json()["id"]
    assert (await client.delete(f"/api/v1/invoices/{invoice_id}")).status_code == 204
    with tenant.include_deleted():
        inv = await db_session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    inv.deleted_at = datetime.now(UTC) - timedelta(days=days_ago)
    await db_session.commit()
    return invoice_id


# --------------------------------------------------------------------------- #
# The property everything else rests on
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_purged_invoice_is_archived_not_merely_destroyed(auth_client, db_session):
    """THE test. Before this, the purge destroyed the row and left an audit
    snapshot; the owner chose to keep the purge running, which makes this the
    only thing standing between a client and permanent loss."""
    org_id = await _org(db_session)
    invoice_id = await _binned(auth_client, db_session, "INV-ARC-1", days_ago=31)

    result = await invoice_service.purge_expired_bin(db_session, org_id)
    await db_session.commit()

    assert result["purged"] == 1
    with tenant.include_deleted():
        assert (
            await db_session.scalar(select(Invoice.id).where(Invoice.id == invoice_id))
        ) is None, "the invoice row should be gone from the live table"

    row = await db_session.scalar(
        select(ArchivedInvoice).where(ArchivedInvoice.original_invoice_id == invoice_id)
    )
    assert row is not None, "the record was destroyed WITHOUT being archived"
    assert row.invoice_number == "INV-ARC-1"
    assert row.vendor_name == "Fictional Fuels OU", "the archive cannot say who it was from"
    assert str(row.total) == "1240.50"


@pytest.mark.asyncio
async def test_the_line_items_survive_with_the_record(auth_client, db_session):
    """An invoice total with no lines behind it cannot answer what was bought,
    which is half of what an accountant needs from a source document."""
    org_id = await _org(db_session)
    invoice_id = await _binned(auth_client, db_session, "INV-ARC-LINES", days_ago=31)

    await invoice_service.purge_expired_bin(db_session, org_id)
    await db_session.commit()

    row = await db_session.scalar(
        select(ArchivedInvoice).where(ArchivedInvoice.original_invoice_id == invoice_id)
    )
    import json

    lines = json.loads(row.line_items_json)
    assert len(lines) == 1
    assert lines[0]["description"] == "Diesel"


@pytest.mark.asyncio
async def test_archiving_and_destroying_are_ONE_transaction(auth_client, db_session):
    """If the archive write fails, the delete must not stand. Otherwise the
    failure mode is silent permanent loss — the exact thing the archive exists to
    prevent, arriving through the archive itself."""
    org_id = await _org(db_session)
    invoice_id = await _binned(auth_client, db_session, "INV-ARC-ATOMIC", days_ago=31)

    async def boom(*a, **kw):
        raise RuntimeError("archive store unavailable")

    original = archive.archive_records
    archive.archive_records = boom
    try:
        with pytest.raises(RuntimeError):
            await invoice_service.purge_expired_bin(db_session, org_id)
        await db_session.rollback()
    finally:
        archive.archive_records = original

    with tenant.include_deleted():
        still_there = await db_session.scalar(select(Invoice.id).where(Invoice.id == invoice_id))
    assert still_there is not None, (
        "the invoice was destroyed even though archiving failed — the two halves "
        "are not in one transaction"
    )


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_included_retention_is_three_years_and_is_stamped_on_the_row(
    auth_client, db_session
):
    """Stamped at write time, not derived on read. Deriving it would mean
    lowering the setting later retroactively destroys records already kept under
    a longer promise — and the client is PAYING for that period."""
    org_id = await _org(db_session)
    await _binned(auth_client, db_session, "INV-ARC-EXP", days_ago=31)

    await invoice_service.purge_expired_bin(db_session, org_id)
    await db_session.commit()

    row = await db_session.scalar(select(ArchivedInvoice))
    years = (row.expires_at - row.archived_at).days / 365
    assert 2.9 < years < 3.1, f"expected the 3-year included tier, got {years:.2f} years"
    assert archive.INCLUDED_RETENTION_YEARS == 3


@pytest.mark.asyncio
async def test_records_nearing_expiry_are_findable_BEFORE_they_go(auth_client, db_session):
    """Three years is likely below the statutory floor in this product's markets,
    so a client who does not extend loses records they were obliged to keep.
    Telling them first is what makes that survivable — and it is the moment the
    paid extension sells itself."""
    org_id = await _org(db_session)
    await _binned(auth_client, db_session, "INV-ARC-SOON", days_ago=31)
    await invoice_service.purge_expired_bin(db_session, org_id)
    await db_session.commit()

    row = await db_session.scalar(select(ArchivedInvoice))
    # Nothing is near expiry today.
    assert await archive.expiring_soon(db_session, org_id) == []

    # A month before it expires, it is.
    almost = row.expires_at - timedelta(days=30)
    due = await archive.expiring_soon(db_session, org_id, now=almost)
    assert [r.id for r in due] == [row.id]

    # And once it is PAST expiry it drops out of the notice: a notice is a
    # warning about the future, and listing already-expired records there would
    # make it useless exactly when it matters.
    after = row.expires_at + timedelta(days=1)
    assert await archive.expiring_soon(db_session, org_id, now=after) == []


# --------------------------------------------------------------------------- #
# Who can read it
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_company_owner_can_read_their_own_archive(auth_client, db_session):
    org_id = await _org(db_session)
    await _binned(auth_client, db_session, "INV-ARC-READ", days_ago=31)
    await invoice_service.purge_expired_bin(db_session, org_id)
    await db_session.commit()

    r = await auth_client.get("/api/v1/archive")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["retention_years"] == 3
    assert body["items"][0]["invoice_number"] == "INV-ARC-READ"


@pytest.mark.asyncio
async def test_roles_below_administrator_cannot_read_the_archive(role_client):
    """The archive holds records a client believes they deleted. "Anyone who can
    read invoices" is the wrong audience, so ARCHIVE_READ is omitted from every
    explicit role set — a grant by omission, which is exactly the kind a later
    edit undoes silently."""
    for role in ("finance_manager", "accountant", "auditor", "user"):
        client = await role_client(role)
        r = await client.get("/api/v1/archive")
        assert r.status_code == 403, f"{role} could read the archive ({r.status_code})"


@pytest.mark.asyncio
async def test_an_administrator_can_read_the_archive(role_client):
    admin = await role_client("admin")

    assert (await admin.get("/api/v1/archive")).status_code == 200


@pytest.mark.asyncio
async def test_one_org_cannot_read_or_fetch_another_orgs_archive(
    auth_client, role_client, db_session
):
    """This table holds the records clients believe they deleted, so its org
    filter is a PRIMARY control rather than a backstop."""
    org_id = await _org(db_session)
    await _binned(auth_client, db_session, "INV-ARC-MINE", days_ago=31)
    await invoice_service.purge_expired_bin(db_session, org_id)
    await db_session.commit()
    mine = await db_session.scalar(select(ArchivedInvoice.id))

    other = await role_client("owner")

    assert (await other.get("/api/v1/archive")).json()["total"] == 0
    # Opaque 404, indistinguishable from an id that never existed (§4.4).
    assert (await other.get(f"/api/v1/archive/{mine}")).status_code == 404
    assert (await other.get(f"/api/v1/archive/{mine}/document")).status_code == 404


@pytest.mark.asyncio
async def test_there_is_no_way_to_restore_from_the_archive(auth_client, db_session):
    """Deliberate absence, asserted so nobody adds one without a decision.
    Re-entering a three-year-old invoice into live books reopens a closed
    accounting period and can collide with numbers issued since."""
    org_id = await _org(db_session)
    await _binned(auth_client, db_session, "INV-ARC-NORESTORE", days_ago=31)
    await invoice_service.purge_expired_bin(db_session, org_id)
    await db_session.commit()
    archived = await db_session.scalar(select(ArchivedInvoice.id))

    r = await auth_client.post(f"/api/v1/archive/{archived}/restore")

    assert r.status_code in (404, 405), (
        "a restore-from-archive route appeared; the archive is read-only by design"
    )
