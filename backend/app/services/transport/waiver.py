"""Receipt-control waivers — the ONLY waivable synthetic-line case (G2.6
slice 3, R15; `BA_fleet_fuel.md` C9, rule "R5 case (a)"). See
`app/models/transport/receipt_waiver.py`'s module docstring for the full
design rationale (grain, FK/cascade choice, why exclusion happens "by
construction" in `claim_lines.build_claim_lines`).

`set_waiver` is the ONLY function that creates a `VatReceiptWaiver` row; it
REFUSES a supplier with ANY registered invoice for the claim's refund
country — reusing `invoice_match.registered_invoices`, never re-deriving
that check a second time (the same drift-prevention discipline
`is_synthetic()`/`derive_product_group()`/`derive_goods_code()` all follow).
`remove_waiver` is the only function that deletes one — basic CRUD
symmetry (an admin who waived the wrong supplier before submission must be
able to undo it), scoped to a `draft` claim like every other pre-submission
mutator in this module family.

WHY BOTH REFUSE ON A NON-`draft` CLAIM
------------------------------------------
A waiver only matters to `build_claim_lines`, which itself refuses to run
on anything but a `draft` claim (G2.4). Allowing a waiver to be set or
removed on a `submitted`/`approved`/`paid` claim would create a row that
can never actually take effect (the claim's lines are already frozen) —
refusing early gives a clear, typed error instead of a silently-inert write.
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionError, ValidationError
from app.models.transport.receipt_waiver import VatReceiptWaiver
from app.models.transport.vat_claim import VatRefundClaim
from app.services import audit, modules
from app.services.transport.invoice_match import registered_invoices


async def _get_claim(db: AsyncSession, org_id: str, claim_id: str) -> VatRefundClaim:
    """Org-scoped lookup — a cross-tenant claim id is indistinguishable from
    an unknown one (master-context §4.4: opaque 404, never 403)."""
    claim = await db.scalar(
        select(VatRefundClaim).where(VatRefundClaim.id == claim_id, VatRefundClaim.org_id == org_id)
    )
    if claim is None:
        raise NotFoundError("Claim not found", code="claim_not_found")
    return claim


async def list_waivers(db: AsyncSession, org_id: str, claim_id: str) -> list[VatReceiptWaiver]:
    """The claim's waiver ROWS, ordered by supplier (WO-77 — the
    route-facing read). Module-gated + opaque-404 claim fetch first, the
    transport-wide entry-point pattern (fails CLOSED, ADR-P3 rule 3 /
    master-context §4.4) — unlike `waived_suppliers`, which is the bare
    internal set read whose callers gate first."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    claim = await _get_claim(db, org_id, claim_id)
    return list(
        await db.scalars(
            select(VatReceiptWaiver)
            .where(VatReceiptWaiver.org_id == org_id, VatReceiptWaiver.claim_id == claim.id)
            .order_by(VatReceiptWaiver.supplier)
        )
    )


async def waived_suppliers(db: AsyncSession, org_id: str, claim_id: str) -> set[str]:
    """Every supplier currently waived on this claim — read by
    `claim_lines.build_claim_lines` (to exclude their transactions) and
    `lock.submit_claim` (to stamp the waiver usage into `status_note`)."""
    rows = await db.scalars(
        select(VatReceiptWaiver.supplier).where(
            VatReceiptWaiver.org_id == org_id, VatReceiptWaiver.claim_id == claim_id
        )
    )
    return set(rows)


async def set_waiver(
    db: AsyncSession, org_id: str, *, claim_id: str, supplier: str
) -> VatReceiptWaiver:
    """R15 — waive `supplier` on `claim_id`'s draft claim. Refuses (422,
    `code="waiver_supplier_has_invoices"`) if the supplier has >=1
    registered invoice for the claim's refund country ("an UNMATCHED
    transaction there is a note-matching fix, not a missing invoice; waiving
    would drop claimable VAT" — C9 verbatim). Idempotent on the (claim,
    supplier) key: a second call returns the existing row unchanged (there
    is no other field to update)."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    claim = await _get_claim(db, org_id, claim_id)
    if claim.status != "draft":
        raise ConflictError(
            f"Claim is '{claim.status}', not 'draft' — cannot waive a supplier",
            code="claim_not_draft",
        )

    existing = await db.scalar(
        select(VatReceiptWaiver).where(
            VatReceiptWaiver.org_id == org_id,
            VatReceiptWaiver.claim_id == claim_id,
            VatReceiptWaiver.supplier == supplier,
        )
    )
    if existing is not None:
        return existing

    candidates = await registered_invoices(
        db, org_id, supplier=supplier, refund_country=claim.refund_country
    )
    if candidates:
        raise ValidationError(
            f"Supplier '{supplier}' has a registered invoice for {claim.refund_country} — "
            "an UNMATCHED transaction there is a note-matching fix, not a missing invoice; "
            "waiving would drop claimable VAT.",
            code="waiver_supplier_has_invoices",
        )

    row = VatReceiptWaiver(org_id=org_id, claim_id=claim_id, supplier=supplier)
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_RECEIPT_WAIVER_SET,
        target_type="vat_refund_claim",
        target_id=claim_id,
        meta={"supplier": supplier, "refund_country": claim.refund_country},
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
    return row


async def remove_waiver(db: AsyncSession, org_id: str, *, claim_id: str, supplier: str) -> bool:
    """Un-waive `supplier` on `claim_id`'s draft claim. Returns whether a row
    was actually removed (a repeat call on an already-unwaived supplier is a
    no-op, returns `False`, audits nothing)."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    claim = await _get_claim(db, org_id, claim_id)
    if claim.status != "draft":
        raise ConflictError(
            f"Claim is '{claim.status}', not 'draft' — cannot remove a waiver",
            code="claim_not_draft",
        )

    result = await db.execute(
        delete(VatReceiptWaiver).where(
            VatReceiptWaiver.org_id == org_id,
            VatReceiptWaiver.claim_id == claim_id,
            VatReceiptWaiver.supplier == supplier,
        )
    )
    await db.flush()
    removed = cast(CursorResult, result).rowcount > 0
    if removed:
        await audit.record(
            db,
            audit.A.TRANSPORT_RECEIPT_WAIVER_REMOVE,
            target_type="vat_refund_claim",
            target_id=claim_id,
            meta={"supplier": supplier},
            org_id=org_id,  # explicit — callable outside an HTTP request context.
        )
    return removed
