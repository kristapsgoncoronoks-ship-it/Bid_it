"""The platform archive — writing to it, and reading it back.

`docs/design/platform-archive.md` is the design; the owner decisions it records
are the reason this module looks the way it does.

WHAT THIS IS FOR
----------------
A client deletes an invoice, it sits in the recycle bin for 30 days, and then the
bin purge destroys the row. Before this module existed, that was the end of it:
the record was gone and only an audit snapshot remained. Now the purge hands the
record here first, and the client's company owner can still view and download it
for the retention period.

That changes what retention IS. A platform-only store is something done TO a
client — a store they did not know about, which is the failure mode the design
doc was written to avoid. A client-visible archive is something they USE, and
"your records are kept for N years and you can look at them" is a sentence that
goes in a DPA and a sales deck rather than one that has to be explained.

READ-ONLY BY DESIGN
-------------------
There is no restore-from-archive. The bin restores into live books; the archive
only shows. Pulling a three-year-old invoice back into the ledger would reopen a
closed accounting period and can collide with invoice numbers issued since —
download is most of the value, re-entering the books is not.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.archived_invoice import ArchivedInvoice

log = logging.getLogger("invoiceiq.archive")

INCLUDED_RETENTION_YEARS = 3
"""What every plan includes (owner decision). Longer is a PAID extension.

Deliberately a number the operator can change rather than a constant compiled
into a sweep — but note that changing it only affects invoices archived AFTERWARDS
(`ArchivedInvoice.expires_at` is stamped at write time), because lowering a
setting must never reach backwards and destroy records already kept under a
longer promise.

NOT VERIFIED AGAINST ANY STATUTE. Baltic accounting law commonly requires source
documents to be kept LONGER than three years, so a client on the included tier
may have records leave the archive while they were still obliged to hold them.
That obligation is theirs rather than the platform's, but it lands on them at the
worst possible moment — which is why nothing may leave the archive without the
owner having been told first, and why the extension must be purchasable after
that notice as well as before it.
"""

EXPIRY_NOTICE_DAYS = 60
"""How long before expiry the owner is warned. Long enough to read an email, talk
to an accountant, and buy the extension without hurrying — the entire point is
that the deadline is never a surprise."""


@dataclass
class ArchivedRecord:
    """One invoice being handed to the archive by the purge.

    A plain value object rather than an ORM row: the purge reads columns (not
    entities) precisely so it does not hydrate rows it is about to destroy, and
    this keeps that property intact across the hand-off.
    """

    original_invoice_id: str
    invoice_number: str | None
    vendor_id: str | None
    vendor_name: str | None
    issue_date: object | None
    currency: str | None
    subtotal: object | None
    tax_amount: object | None
    total: object | None
    line_items: list[dict]
    source_sha256: str | None
    source_filename: str | None
    original_deleted_at: datetime | None
    original_deleted_by: str | None


async def retention_years(db: AsyncSession, org_id: str) -> int:
    """How long THIS organisation's archive keeps a record.

    The included tier for now; the paid extension will read a per-org value here
    and nothing else in the module will need to change, which is why the lookup
    exists as a function before it has anything to look up.
    """
    return INCLUDED_RETENTION_YEARS


async def archive_records(
    db: AsyncSession, org_id: str, records: list[ArchivedRecord], *, now: datetime | None = None
) -> int:
    """Write records to the archive. Returns how many landed.

    Called by `invoices.purge_expired_bin` INSIDE the same transaction as the
    delete, so a record can never be destroyed without being archived — the two
    halves commit together or not at all. That single property is what makes the
    archive a backstop rather than a best-effort copy.

    Does NOT commit; the caller owns the transaction.
    """
    if not records:
        return 0

    stamp = now or datetime.now(UTC)
    years = await retention_years(db, org_id)
    # timedelta has no "years", and calendar arithmetic on 29 February is a
    # famous way to raise ValueError once every four years. 365-day years are
    # close enough for a retention window measured in years and never throw.
    expires = stamp + timedelta(days=365 * years)

    for r in records:
        db.add(
            ArchivedInvoice(
                org_id=org_id,
                original_invoice_id=r.original_invoice_id,
                invoice_number=r.invoice_number,
                vendor_id=r.vendor_id,
                vendor_name=r.vendor_name,
                issue_date=r.issue_date,
                currency=r.currency,
                subtotal=r.subtotal,
                tax_amount=r.tax_amount,
                total=r.total,
                line_items_json=json.dumps(r.line_items, default=str),
                source_sha256=r.source_sha256,
                source_filename=r.source_filename,
                original_deleted_at=r.original_deleted_at,
                original_deleted_by=r.original_deleted_by,
                archived_at=stamp,
                expires_at=expires,
            )
        )
    return len(records)


@dataclass
class ArchivePage:
    items: list[ArchivedInvoice]
    total: int
    retention_years: int


async def page(db: AsyncSession, org_id: str, *, limit: int = 50, offset: int = 0) -> ArchivePage:
    """One page of an organisation's archive, newest first.

    Ordinary tenant-scoped reads: this table is in `TENANT_MODELS`, so the
    central guard applies its org filter as well as the explicit one here. That
    belt-and-braces matters more on this table than on most — it is the one
    holding records clients believe they deleted.
    """
    total = await db.scalar(
        select(func.count()).select_from(ArchivedInvoice).where(ArchivedInvoice.org_id == org_id)
    )
    rows = await db.scalars(
        select(ArchivedInvoice)
        .where(ArchivedInvoice.org_id == org_id)
        .order_by(ArchivedInvoice.archived_at.desc(), ArchivedInvoice.id)
        .limit(limit)
        .offset(offset)
    )
    return ArchivePage(
        items=list(rows),
        total=int(total or 0),
        retention_years=await retention_years(db, org_id),
    )


async def get(db: AsyncSession, org_id: str, archive_id: str) -> ArchivedInvoice | None:
    """One archived record, org-scoped. `None` rather than raising, so the caller
    chooses the 404 shape (opaque, §4.4)."""
    return await db.scalar(
        select(ArchivedInvoice).where(
            ArchivedInvoice.org_id == org_id, ArchivedInvoice.id == archive_id
        )
    )


async def expiring_soon(
    db: AsyncSession, org_id: str, *, now: datetime | None = None
) -> list[ArchivedInvoice]:
    """Records due to leave the archive within `EXPIRY_NOTICE_DAYS`.

    The notice this feeds is not a nicety. Three years is likely BELOW the
    statutory floor in the markets this product serves, so a client who does not
    extend will lose records they were obliged to keep. Telling them first is
    what makes that survivable — and it is also the moment the extension sells
    itself, which is why the same query serves both.
    """
    stamp = now or datetime.now(UTC)
    horizon = stamp + timedelta(days=EXPIRY_NOTICE_DAYS)
    rows = await db.scalars(
        select(ArchivedInvoice)
        .where(
            ArchivedInvoice.org_id == org_id,
            ArchivedInvoice.expires_at <= horizon,
            ArchivedInvoice.expires_at > stamp,
        )
        .order_by(ArchivedInvoice.expires_at)
    )
    return list(rows)
