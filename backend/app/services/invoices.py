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

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, WorkflowState
from app.services import bulk


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
# Bulk delete (L-4 slice 3) — the first IRREVERSIBLE bulk operation.
# --------------------------------------------------------------------------- #


@dataclass
class DeletedSnapshot:
    """What one invoice WAS, captured before it stopped existing.

    Guard 3 says the reversal record is derived mechanically from the write. For a
    delete, an id alone is not a reversal basis — the row is gone, so the id
    identifies nothing. These fields are read off the row immediately before it is
    destroyed, so the audit trail can still answer "what did we delete?" without
    anyone having hand-written an inverse.
    """

    invoice_id: str
    invoice_number: str | None
    vendor_id: str | None
    issue_date: str | None
    currency: str | None
    total: str | None


async def bulk_delete_drafts(
    db: AsyncSession,
    org_id: str,
    *,
    ids: list[str],
    selection: bulk.Selection,
    agreed_count: int | None,
) -> tuple[bulk.BulkResult, list[DeletedSnapshot]]:
    """Delete DRAFT invoices in one action, under the L-4 guards.

    This is the first bulk operation that cannot be undone, so guard 4 fires here
    for real: a filter-based selection is REFUSED outright. "Everything matching
    this filter" is a set the operator never enumerated and cannot verify, and the
    difference between what they believed they were destroying and what the server
    resolves is unrecoverable.

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
        if inv.workflow_state is not WorkflowState.draft:
            result.outcomes.append(
                bulk.Outcome(
                    invoice_id,
                    bulk.SKIPPED,
                    f"Only a draft can be deleted; this one is {inv.workflow_state.value}.",
                )
            )
            continue
        if inv.amount_paid and inv.amount_paid > 0:
            result.outcomes.append(
                bulk.Outcome(invoice_id, bulk.SKIPPED, "A payment is recorded against it.")
            )
            continue
        if inv.payment_run_id:
            result.outcomes.append(
                bulk.Outcome(invoice_id, bulk.SKIPPED, "It is part of a payment run.")
            )
            continue

        # Snapshot BEFORE the delete — afterwards there is nothing left to read.
        snapshots.append(
            DeletedSnapshot(
                invoice_id=inv.id,
                invoice_number=inv.invoice_number,
                vendor_id=inv.vendor_id,
                issue_date=inv.issue_date.isoformat() if inv.issue_date else None,
                currency=inv.currency,
                total=str(inv.total) if inv.total is not None else None,
            )
        )
        await db.delete(inv)
        result.outcomes.append(bulk.Outcome(invoice_id, bulk.APPLIED))
    return result, snapshots
