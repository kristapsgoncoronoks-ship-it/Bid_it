"""Minimal read-only AP invoice queries shared across domains.

This module exists so a caller OUTSIDE the AP domain never has to import
`app.models.invoice` directly — ADR-P3 rule 2 (`docs/plan/plan-a/ARCH_plan.md`
"the transport vertical reads the core through services... never a raw model
join") names an `invoice_service` seam that did not exist in this codebase
until the transport vertical's note→invoice resolution
(`app.services.transport.invoice_match`, G2.4) needed exactly this: "which
invoices are REGISTERED for this vendor" without a cross-domain model import.

Business logic for invoice CREATE/UPDATE/the review-and-approval lifecycle
stays where it already lives (`app.services.invoice_workflow`, the `invoices`
route); this module is deliberately small and read-only.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import tenant
from app.core.errors import ConflictError, NotFoundError
from app.models.extraction_run import ExtractionRun
from app.models.invoice import Invoice, LineItem
from app.models.vendor import Vendor
from app.services import bulk, deletion_consent

log = logging.getLogger("invoiceiq.invoices")


async def list_by_vendor(db: AsyncSession, org_id: str, vendor_id: str) -> list[Invoice]:
    """Every invoice for one vendor, ordered by `invoice_number` for
    deterministic resolution. A "registered invoice" in the R2/R16 sense
    (`docs/plan/shared/specs/BA_fleet_fuel.md` C1/C3) is exactly a row here."""
    rows = await db.scalars(
        select(Invoice)
        .where(Invoice.org_id == org_id, Invoice.vendor_id == vendor_id)
        .order_by(Invoice.invoice_number)
    )
    return list(rows)


async def get_by_id(db: AsyncSession, org_id: str, invoice_id: str) -> Invoice | None:
    """Org-scoped lookup by id — returns `None` rather than raising (mirrors
    `app.services.issuer.get_by_id`'s contract) so a caller decides the 404
    shape for its own domain."""
    return await db.scalar(
        select(Invoice).where(Invoice.org_id == org_id, Invoice.id == invoice_id)
    )


# --------------------------------------------------------------------------- #
# Delete and restore — the recycle bin (docs/design/deletion-and-archive.md).
#
# Deleting an invoice no longer destroys the row: it stamps `deleted_at`, and the
# central guard in `app.core.tenant` hides the row from every read. For 30 days
# the record can be restored by an admin or the org owner; after that the purge
# takes it (a later step).
# --------------------------------------------------------------------------- #

BIN_RETENTION_DAYS = 30
"""Fixed, not per-tenant configurable (owner decision). One number is easy to
state in a UI and in a contract; a per-tenant one is a support question forever."""

PURGE_BATCH = 500
"""Rows destroyed per statement. Bounded so a large bin cannot exceed a driver's
bind-parameter ceiling (Postgres 65535, SQLite 32766) — which would make the job
fail, be re-enqueued daily, and fail forever, so the bin never empties."""


class ConsentRequiredError(ConflictError):
    """The deletion is consequential and has not been acknowledged.

    Carries the CURRENT warning so the caller can show it and retry. A 409 that
    merely said "consent required" would leave the client to compose its own
    wording, which is how the words on screen and the words in the audit trail
    come apart.
    """

    code = "deletion_consent_required"

    def __init__(self, inv: Invoice):
        super().__init__(
            "This invoice is not a plain draft. Read the warning and confirm to delete it."
        )
        self._warning = deletion_consent.warning_for(inv)

    def extra(self) -> dict:
        return {"warning": self._warning}


@dataclass
class DeletedSnapshot:
    """What one invoice WAS at the moment it was binned.

    Guard 3 says the reversal record is derived mechanically from the write. The
    row now survives the delete, so this is no longer the only trace of it — but
    it is still what the audit event carries, because an audit entry saying only
    "id X deleted" forces a reader to go and look up a row whose values may since
    have changed. What the record looked like when the decision was taken is the
    thing worth freezing.
    """

    invoice_id: str
    invoice_number: str | None
    vendor_id: str | None
    issue_date: str | None
    currency: str | None
    total: str | None


def deletion_refusal(inv: Invoice) -> str | None:
    """Why this invoice must not be deleted IN BULK, or None when it may be.

    Derived from `deletion_consent.consequences_for` — the same facts the
    one-at-a-time path reads, so the two can never disagree about the state of an
    invoice even though they apply different policies to it.

    The policies differ deliberately. The owner's decision is that a client may
    delete a consequential invoice, warned every time and with the confirmation
    recorded (`delete_one`). "Every time" is precisely what a bulk action cannot
    deliver: one checkbox covering two hundred invoices is a single consent
    standing in for two hundred decisions, several of which the operator has not
    looked at. So bulk stays DRAFTS ONLY and refuses the rest as skips.
    """
    consequences = deletion_consent.consequences_for(inv)
    if not consequences:
        return None
    return (
        "Only a draft can be deleted in bulk. "
        + consequences[0].message
        + " Delete it on its own if you intend to."
    )


def deletion_snapshot(inv: Invoice) -> DeletedSnapshot:
    """What this invoice IS at the moment of the decision, frozen into the audit
    event. Read off the live row; never reconstructed."""
    return DeletedSnapshot(
        invoice_id=inv.id,
        invoice_number=inv.invoice_number,
        vendor_id=inv.vendor_id,
        issue_date=inv.issue_date.isoformat() if inv.issue_date else None,
        currency=inv.currency,
        total=str(inv.total) if inv.total is not None else None,
    )


async def claim_backing_refusal(db: AsyncSession, org_id: str, invoice_id: str) -> str | None:
    """WO-M (owner decision 2026-08-15: "add the claim link then refuse").

    An invoice a FROZEN claim line resolved to, on a claim that is filed
    (submitted/approved/paid) or adversely decided (rejected), is EVIDENCE —
    a government filing references it. Deleting it is a hard refusal, not a
    consent: no warning text makes destroying filed evidence acceptable. A
    withdrawn claim released the invoice; a draft rebuilds its lines at will.
    The real link is `vat_claim_lines.invoice_id` set at resolution — no
    string heuristics — read through the canonical transport query registry."""
    from app.services.transport import queries as transport_queries

    claims = list(await db.scalars(transport_queries.claims_backed_by_invoice(org_id, invoice_id)))
    if not claims:
        return None
    named = ", ".join(f"{c.refund_country} {c.ref_period} ({c.status})" for c in claims[:3])
    more = "" if len(claims) <= 3 else f" and {len(claims) - 3} more"
    return (
        f"This invoice is frozen evidence behind a filed VAT refund claim: {named}{more}. "
        "Deletion is refused while the claim stands — withdraw the claim first if you "
        "really intend this."
    )


class ClaimEvidenceError(ConflictError):
    """The invoice backs a filed VAT claim — deletion is refused, not consented."""

    code = "invoice_backs_filed_claim"


def _bin(inv: Invoice, actor: str | None) -> None:
    """Move ONE loaded invoice into the bin. The only place `deleted_at` is set,
    so "what does deleting actually do" has a single answer."""
    inv.deleted_at = datetime.now(UTC)
    inv.deleted_by = actor


async def delete_one(
    db: AsyncSession,
    org_id: str,
    invoice_id: str,
    *,
    actor: str | None = None,
    acknowledged_warning_version: str | None = None,
) -> tuple[DeletedSnapshot, dict | None]:
    """Move ONE invoice to the bin. Returns the snapshot and, when the deletion
    was a consequential one, the consent record for the audit event.

    The row is NOT destroyed — it is stamped `deleted_at` and disappears from
    every read (`app.core.tenant`), recoverable for `BIN_RETENTION_DAYS`.

    A CLEAN DRAFT deletes with no ceremony. Anything past draft, paid, or in a
    payment run is the client's decision to make (owner decision) but only once
    they have been warned: without a matching `acknowledged_warning_version` this
    raises `ConsentRequiredError` carrying the current warning, so the words the
    client sees are always the server's and can never drift from what the audit
    trail records they accepted. An acknowledgement quoting an OLDER version is
    refused for the same reason — consent to different words is not consent to
    these.

    Raises NotFoundError for an unknown, cross-tenant, or ALREADY-BINNED id (all
    indistinguishable — an opaque 404, §4.4; a binned invoice is invisible to
    this query for exactly the same reason a foreign tenant's is). Does NOT
    commit; the caller commits with the audit event so the deletion and the
    record of it are one transaction.
    """
    inv = await db.scalar(select(Invoice).where(Invoice.org_id == org_id, Invoice.id == invoice_id))
    if inv is None:
        raise NotFoundError("Invoice not found")

    # WO-M: filed-claim evidence is a hard refusal BEFORE the consent ceremony —
    # no acknowledgement makes destroying it acceptable.
    refusal = await claim_backing_refusal(db, org_id, invoice_id)
    if refusal is not None:
        raise ClaimEvidenceError(refusal)

    consent: dict | None = None
    if deletion_consent.requires_consent(inv):
        if acknowledged_warning_version != deletion_consent.WARNING_VERSION:
            raise ConsentRequiredError(inv)
        consent = deletion_consent.audit_record(inv)

    snap = deletion_snapshot(inv)
    _bin(inv, actor)
    return snap, consent


async def bulk_delete_drafts(
    db: AsyncSession,
    org_id: str,
    *,
    ids: list[str],
    selection: bulk.Selection,
    agreed_count: int | None,
    actor: str | None = None,
) -> tuple[bulk.BulkResult, list[DeletedSnapshot]]:
    """Move DRAFT invoices to the bin in one action, under the L-4 guards.

    Guard 4 (a filter-based selection is refused) is KEPT even though the bin has
    made this reversible and guard 4's stated justification therefore no longer
    holds. Reversible is not the same as harmless: "everything matching this
    filter" is still a set the operator never enumerated, and 300 invoices
    vanishing from the books is a real incident for the hours or days before
    anyone reconstructs which ones they were. The guard moves to the permanent
    purge when that lands; it does not simply disappear here.

    Deliberately stricter than the single-invoice delete route, which today
    removes an invoice in ANY state including paid. Rather than quietly widen this
    to match, the narrow rule is applied here and the discrepancy is recorded — a
    bulk action is the wrong place to inherit a per-record gap, because it
    multiplies it.

    Every refusal is a SKIP with a reason naming the state, not a failure: an
    approved or paid invoice being left alone is the system working.

    Does NOT commit — the caller commits the batch with its audit event, so the
    deletions and the record of them are one transaction.
    """
    bulk.require_explicit_selection(selection, action="Deleting invoices")
    clean = bulk.normalise_ids(ids)
    bulk.check_agreed_count(clean, agreed_count)

    result = bulk.BulkResult()
    snapshots: list[DeletedSnapshot] = []
    for invoice_id in clean:
        inv = await db.scalar(
            select(Invoice).where(Invoice.org_id == org_id, Invoice.id == invoice_id)
        )
        if inv is None:
            result.outcomes.append(
                bulk.Outcome(invoice_id, bulk.SKIPPED, "Not an invoice in this workspace.")
            )
            continue
        refusal = deletion_refusal(inv)
        if refusal:
            # The SAME rule the single-delete route enforces — a skip here and a
            # 409 there are the same decision, worded identically.
            result.outcomes.append(bulk.Outcome(invoice_id, bulk.SKIPPED, refusal))
            continue
        # WO-M: a DRAFT AP invoice can still be frozen claim evidence (the
        # resolver links lines regardless of workflow state) — same hard
        # refusal as the single delete, surfaced as a skip.
        claim_refusal = await claim_backing_refusal(db, org_id, invoice_id)
        if claim_refusal:
            result.outcomes.append(bulk.Outcome(invoice_id, bulk.SKIPPED, claim_refusal))
            continue

        snapshots.append(deletion_snapshot(inv))
        _bin(inv, actor)
        result.outcomes.append(bulk.Outcome(invoice_id, bulk.APPLIED))
    return result, snapshots


# --------------------------------------------------------------------------- #
# The bin: what is in it, and getting something back out.
# --------------------------------------------------------------------------- #


@dataclass
class BinnedInvoice:
    """One row of the Trash screen."""

    invoice_id: str
    invoice_number: str | None
    vendor_name: str | None
    issue_date: str | None
    currency: str | None
    total: str | None
    deleted_at: str
    deleted_by: str | None
    days_left: int


def _days_left(deleted_at: datetime) -> int:
    """Whole days before the purge may take this record, floored at 0.

    Floored rather than allowed to go negative because a record the purge has not
    yet collected is still restorable, and showing "-2 days" invites the reading
    that it is already gone when it is not.
    """
    if deleted_at.tzinfo is None:  # SQLite hands back naive datetimes
        deleted_at = deleted_at.replace(tzinfo=UTC)
    elapsed = datetime.now(UTC) - deleted_at
    return max(0, BIN_RETENTION_DAYS - elapsed.days)


async def binned_page(
    db: AsyncSession, org_id: str, *, limit: int = 50, offset: int = 0
) -> tuple[list[BinnedInvoice], int]:
    """The bin's contents, newest deletion first, plus the total count.

    The ONLY read in the AP domain that opts out of the hiding guard, and it does
    so for the length of two statements. Ordering is by deletion time because the
    question a Trash screen answers is "what did I just delete?", not "which
    invoice is oldest".
    """
    with tenant.include_deleted():
        total = await db.scalar(
            select(func.count())
            .select_from(Invoice)
            .where(Invoice.org_id == org_id, Invoice.deleted_at.is_not(None))
        )
        rows = await db.scalars(
            select(Invoice)
            .where(Invoice.org_id == org_id, Invoice.deleted_at.is_not(None))
            .order_by(Invoice.deleted_at.desc(), Invoice.id)
            .limit(limit)
            .offset(offset)
            .options(selectinload(Invoice.vendor))
        )
        items: list[BinnedInvoice] = []
        for inv in rows:
            binned_at = inv.deleted_at
            if binned_at is None:  # pragma: no cover - excluded by the query above
                continue
            items.append(
                BinnedInvoice(
                    invoice_id=inv.id,
                    invoice_number=inv.invoice_number,
                    vendor_name=inv.vendor.name if inv.vendor else None,
                    issue_date=inv.issue_date.isoformat() if inv.issue_date else None,
                    currency=inv.currency,
                    total=str(inv.total) if inv.total is not None else None,
                    deleted_at=binned_at.isoformat(),
                    deleted_by=inv.deleted_by,
                    days_left=_days_left(binned_at),
                )
            )
    return items, int(total or 0)


async def restore_one(db: AsyncSession, org_id: str, invoice_id: str) -> Invoice:
    """Bring ONE binned invoice back into the books.

    Refuses when a LIVE invoice already carries the same number. The scenario is
    concrete and expensive: someone deletes INV-001 by mistake, re-keys it as a
    new invoice, then restores the original — and the organisation now holds two
    live copies of one supplier bill, which is how a bill gets paid twice. There
    is no database constraint stopping that (two invoices may legitimately share a
    number across vendors), so the check lives here, where the duplicate would be
    created by a machine rather than chosen by a person.

    Raises NotFoundError for an unknown, cross-tenant or NOT-binned id (opaque,
    §4.4). Does NOT commit; the caller commits with the audit event.
    """
    with tenant.include_deleted():
        inv = await db.scalar(
            select(Invoice).where(
                Invoice.org_id == org_id,
                Invoice.id == invoice_id,
                Invoice.deleted_at.is_not(None),
            )
        )
    if inv is None:
        raise NotFoundError("Invoice not found")

    if inv.invoice_number:
        # Outside include_deleted() ON PURPOSE: only a LIVE duplicate is a
        # conflict. Two records sitting in the bin with the same number are not a
        # problem until one of them comes back, and checking against binned rows
        # too would make the bin progressively harder to escape the more it held.
        #
        # Scoped to the SAME VENDOR. The docstring above says two invoices may
        # legitimately share a number across vendors, and the first version of
        # this check then ignored that and compared org-wide — refusing to give a
        # client their record back because an unrelated supplier happened to use
        # the same number, and telling them to go and resolve that one. A false
        # refusal on the RECOVERY path is worse than the duplicate it prevents.
        clash = await db.scalar(
            select(Invoice.id)
            .where(
                Invoice.org_id == org_id,
                Invoice.vendor_id == inv.vendor_id,
                Invoice.invoice_number == inv.invoice_number,
                Invoice.id != inv.id,
            )
            .limit(1)
        )
        if clash:
            raise ConflictError(
                f"A live invoice from this supplier already uses number "
                f"{inv.invoice_number}. Resolve that one before restoring this.",
                code="invoice_number_in_use",
            )

    inv.deleted_at = None
    inv.deleted_by = None
    return inv


# --------------------------------------------------------------------------- #
# The 30-day purge (docs/design/deletion-and-archive.md, step 4).
# --------------------------------------------------------------------------- #


async def purge_expired_bin(db: AsyncSession, org_id: str, *, now: datetime | None = None) -> dict:
    """Remove invoices whose 30 days in the bin have run out.

    REFUSES (no-op) while a legal hold is active, reusing `retention.is_on_hold`
    rather than a second check of its own. A hold is a preservation duty that
    overrides data minimisation, and a bin that destroyed records on day 31
    regardless would be the exact failure the hold exists to prevent — arriving
    through the one deletion path nobody thought to wire it into.

    Without this the bin is a leak in the other direction: records sit invisible
    forever while the client is told they go after 30 days, which is a storage-
    limitation problem rather than a loss-of-data one, but a problem either way.

    Runs INSIDE `include_deleted()` for the selection, since the rows it is
    looking for are by definition hidden from every ordinary read.

    NOTE — for now this DESTROYS the row. The design doc's platform archive
    (step 6) is where a purged record is meant to go instead; until it exists,
    the audit event's snapshot of each destroyed invoice is the only remaining
    trace, which is why it records the full record and not just an id.

    Returns `{"held": bool, "purged": int, "records": [...]}`. Never raises on a
    business outcome.
    """
    from app.services import retention

    if await retention.is_on_hold(db, org_id):
        return {"held": True, "purged": 0, "records": []}

    cutoff = (now or datetime.now(UTC)) - timedelta(days=BIN_RETENTION_DAYS)
    records: list[dict] = []
    purged = 0

    while True:
        # ONE BATCH AT A TIME. An unbounded `id.in_(...)` hits Postgres's 65535
        # bind-parameter ceiling (SQLite's is 32766) and the job then fails, is
        # re-enqueued daily, and fails forever — so the bin would never empty,
        # inverting the exact promise this function exists to keep. Columns, not
        # entities: hydrating rows about to be destroyed just to read six scalars
        # off them costs hundreds of MB on a large bin.
        with tenant.include_deleted():
            batch = list(
                await db.execute(
                    select(
                        Invoice.id,
                        Invoice.invoice_number,
                        Invoice.vendor_id,
                        Invoice.issue_date,
                        Invoice.currency,
                        Invoice.subtotal,
                        Invoice.tax_amount,
                        Invoice.total,
                        Invoice.source_filename,
                        Invoice.deleted_at,
                        Invoice.deleted_by,
                    )
                    .where(
                        Invoice.org_id == org_id,
                        Invoice.deleted_at.is_not(None),
                        Invoice.deleted_at < cutoff,
                    )
                    .limit(PURGE_BATCH)
                )
            )
        if not batch:
            break

        ids = [row.id for row in batch]
        records += [
            asdict(
                DeletedSnapshot(
                    invoice_id=row.id,
                    invoice_number=row.invoice_number,
                    vendor_id=row.vendor_id,
                    issue_date=row.issue_date.isoformat() if row.issue_date else None,
                    currency=row.currency,
                    total=str(row.total) if row.total is not None else None,
                )
            )
            for row in batch
        ]

        # ARCHIVE BEFORE DESTROYING — the whole point of the archive.
        #
        # Written inside the SAME transaction as the delete below, so a record
        # can never be destroyed without being archived: the two halves commit
        # together or not at all. That single property is what makes this a
        # backstop rather than a best-effort copy.
        await _archive_batch(db, org_id, batch, ids)

        # UNLINK the composite-FK referrers BEFORE the parent goes.
        #
        # `vat_claim_lines` and `fuel_transactions` reference invoices through a
        # COMPOSITE key `(org_id, invoice_id)` declared `ON DELETE SET NULL`.
        # SET NULL on a multi-column FK nulls EVERY referencing column — org_id
        # included — and org_id is NOT NULL on both tables, so the database
        # raises rather than nulling. Verified directly:
        #     NOT NULL constraint failed: fuel_transactions.org_id
        # This repo already documents the trap in the `vat_note_invoice_overrides`
        # migration, which chose CASCADE for exactly this reason; these two
        # tables never got the same treatment.
        #
        # Left unhandled, the daily purge raises for any tenant using the
        # transport module, retries, dead-letters, and repeats tomorrow — so that
        # tenant's bin is never emptied and its records are invisible AND
        # immortal, the precise failure this function exists to prevent.
        #
        # Nulling `invoice_id` ourselves keeps the referencing row and its
        # tenancy intact (a fuel transaction is analytics history the client did
        # not ask to delete) and means the FK's SET NULL never fires.
        await _unlink_purged_invoices(db, org_id, ids)

        # Children first, then the parent — identical behaviour on SQLite and
        # Postgres regardless of whether FK cascades are enforced, the same
        # reason `retention.purge` does it this way.
        await db.execute(delete(LineItem).where(LineItem.invoice_id.in_(ids)))
        # The org and bin predicates are RE-ASSERTED on the delete itself, not
        # inherited from the select above. Two reasons, both real:
        #   - the tenant guard does NOT touch a non-SELECT statement, so without
        #     `org_id` here one WHERE clause in one SELECT is the entire tenant
        #     boundary on the only irreversible path in the product;
        #   - a `restore_one` that commits between the select and the delete
        #     would otherwise be silently undone, destroying a record a client
        #     had just recovered — and day 29-31 is exactly when they go looking.
        result = await db.execute(
            delete(Invoice).where(
                Invoice.org_id == org_id,
                Invoice.id.in_(ids),
                Invoice.deleted_at.is_not(None),
                Invoice.deleted_at < cutoff,
            )
        )
        purged += int(getattr(result, "rowcount", 0) or 0)

        if len(batch) < PURGE_BATCH:
            break

    return {"held": False, "purged": purged, "records": records}


async def _unlink_purged_invoices(db: AsyncSession, org_id: str, ids: list[str]) -> None:
    """Clear `invoice_id` on rows whose composite FK would otherwise SET NULL.

    Imported locally, like `retention`'s own cross-domain reach: the AP service
    must not take a module-level dependency on the transport vertical, but the
    database constraint is real and has to be satisfied somewhere. If a future
    table gains the same `(org_id, invoice_id) ON DELETE SET NULL` shape it must
    be added here — `tests/test_recycle_bin_purge_fk.py` fails loudly if one is
    missed, which is the intended way to find out.
    """
    from app.models.transport.fuel_transaction import FuelTransaction
    from app.models.transport.vat_claim import VatRefundClaimLine

    for model in (FuelTransaction, VatRefundClaimLine):
        await db.execute(
            update(model)
            .where(model.org_id == org_id, model.invoice_id.in_(ids))
            .values(invoice_id=None)
        )


async def _archive_batch(db: AsyncSession, org_id: str, batch: list, ids: list[str]) -> None:
    """Hand one batch of expiring invoices to the platform archive.

    THE SOURCE DOCUMENT IS DELIBERATELY NOT DELETED HERE ANY MORE. An earlier
    version of the purge removed the stored PDF, on the reasoning that leaving it
    made "it leaves your workspace" untrue. The owner has since decided the
    archive keeps the record AND the document, because the PDF is what proves
    anything to a tax authority years later — which is most of the reason to
    archive at all. The bytes now live exactly as long as the archive row that
    points at them, and are collected when THAT expires rather than here.

    Line items and the document hash are read before the delete: `line_items`
    goes with the parent, and `extraction_runs` cascades away with it, taking the
    last pointer to the bytes.
    """
    from app.services import archive

    shas = {
        row.invoice_id: row.source_sha256
        for row in await db.execute(
            select(ExtractionRun.invoice_id, ExtractionRun.source_sha256).where(
                ExtractionRun.org_id == org_id,
                ExtractionRun.invoice_id.in_(ids),
                ExtractionRun.source_sha256.is_not(None),
            )
        )
    }
    lines: dict[str, list[dict]] = {}
    for li in await db.execute(
        select(
            LineItem.invoice_id,
            LineItem.description,
            LineItem.category,
            LineItem.quantity,
            LineItem.unit_price,
            LineItem.amount,
            LineItem.tax_rate,
        ).where(LineItem.invoice_id.in_(ids))
    ):
        lines.setdefault(li.invoice_id, []).append(
            {
                "description": li.description,
                "category": li.category,
                "quantity": str(li.quantity) if li.quantity is not None else None,
                "unit_price": str(li.unit_price) if li.unit_price is not None else None,
                "amount": str(li.amount) if li.amount is not None else None,
                "tax_rate": str(li.tax_rate) if li.tax_rate is not None else None,
            }
        )

    vendors = {
        v.id: v.name
        for v in await db.execute(
            select(Vendor.id, Vendor.name).where(
                Vendor.org_id == org_id,
                Vendor.id.in_({row.vendor_id for row in batch if row.vendor_id}),
            )
        )
    }

    await archive.archive_records(
        db,
        org_id,
        [
            archive.ArchivedRecord(
                original_invoice_id=row.id,
                invoice_number=row.invoice_number,
                vendor_id=row.vendor_id,
                vendor_name=vendors.get(row.vendor_id),
                issue_date=row.issue_date,
                currency=row.currency,
                subtotal=row.subtotal,
                tax_amount=row.tax_amount,
                total=row.total,
                line_items=lines.get(row.id, []),
                source_sha256=shas.get(row.id),
                source_filename=row.source_filename,
                original_deleted_at=row.deleted_at,
                original_deleted_by=row.deleted_by,
            )
            for row in batch
        ],
    )
