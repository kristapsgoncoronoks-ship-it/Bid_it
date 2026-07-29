"""Claim-line construction — the LIVE, unfrozen `VatRefundClaimLine` builder
(G2.4; `BA_fleet_fuel.md` C1, R2).

`build_claim_lines` is the transport-vertical analogue of Fleet Fuel's
`vat_refund.invoice_lines()`: one row per (invoice, product_group), grouping
every `FuelTransaction` in the claim's (entity, refund_country, period) scope
via `invoice_match.resolve_invoice_ref()` (G2.4's note→invoice resolution).
Unlike Fleet Fuel — which derived claim lines LIVE at read time forever,
protected only by keeping the legal database physically separate — this
codebase MATERIALIZES the lines as real `VatRefundClaimLine` rows so a future
freeze (G2.5, "frozen claim lines at submission") has something concrete to
stamp `frozen_at` on. This module only ever touches `frozen_at IS NULL` rows
and refuses to run at all once a claim leaves `draft` — see
`build_claim_lines`'s docstring for why.

This module deliberately does NOT: freeze anything (`frozen_at` stays NULL
here — G2.5), derive a goods_code (Art. 9 mapping is G2.8, independent of
this order), or populate `vat_id` on a line (no per-line VAT-id capture
exists yet; a synthetic line is still fully detected via `invoice_ref` alone,
since `claim_gates.is_synthetic()` treats either input as sufficient).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionError
from app.core.money import q2
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.services import audit, modules
from app.services.transport.invoice_match import resolve_invoice_ref

UNMATCHED = "UNMATCHED"


def _period_months(ref_period: str) -> list[str]:
    """`"YYYY-Qn"` / `"YYYY-YEAR"` -> the list of `"YYYY-MM"` months it spans
    (Art. 16: a quarter is 3 months, a year is 12) — `ref_period` is trusted
    here to already match `claim.validate_ref_period`'s shape (enforced at
    claim creation + the DB CHECK constraint), so no re-validation happens in
    this purely-derived helper."""
    year, tail = ref_period.split("-", 1)
    if tail == "YEAR":
        return [f"{year}-{m:02d}" for m in range(1, 13)]
    quarter = int(tail[1])
    start_month = (quarter - 1) * 3 + 1
    return [f"{year}-{m:02d}" for m in range(start_month, start_month + 3)]


async def _get_claim(db: AsyncSession, org_id: str, claim_id: str) -> VatRefundClaim:
    """Org-scoped lookup — a cross-tenant claim id is indistinguishable from
    an unknown one (master-context §4.4: opaque 404, never 403)."""
    claim = await db.scalar(
        select(VatRefundClaim).where(VatRefundClaim.id == claim_id, VatRefundClaim.org_id == org_id)
    )
    if claim is None:
        raise NotFoundError("Claim not found", code="claim_not_found")
    return claim


async def build_claim_lines(
    db: AsyncSession, org_id: str, claim_id: str
) -> list[VatRefundClaimLine]:
    """(Re)build the live, unfrozen claim lines for a `draft` claim from the
    underlying `fuel_transactions` in its (entity, refund_country, period)
    scope — R2 (one row per (invoice, product_group), never an `"ALL:"`
    aggregate) via `invoice_match.resolve_invoice_ref()` (R16).

    Refuses (409, `code="claim_not_draft"`) unless the claim is `draft`: once
    a future G2.5 freezes a submitted claim's lines (`frozen_at` stamped),
    rebuilding them here would violate ADR-P3's "a re-close cannot alter a
    filed claim" — this function only ever DELETEs+re-INSERTs rows where
    `frozen_at IS NULL`, so even if it were ever called on a claim carrying
    frozen lines (not reachable via any code path today — no freeze service
    exists yet), a frozen row would survive untouched. The `claim_not_draft`
    refusal is the primary guard; the `frozen_at IS NULL` scoping on both the
    DELETE and the set of rows this function ever writes is the belt-and-
    braces second layer for the day G2.5 exists.

    Gated on the `transport` module entitlement first, identical pattern to
    every other transport service (`modules.is_enabled`, never
    `modules.require_enabled` — a service raises `AppError`, never
    `fastapi.HTTPException`).
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    claim = await _get_claim(db, org_id, claim_id)
    if claim.status != "draft":
        raise ConflictError(
            f"Claim is '{claim.status}', not 'draft' — cannot rebuild claim lines",
            code="claim_not_draft",
        )

    months = _period_months(claim.ref_period)
    txns = list(
        await db.scalars(
            select(FuelTransaction).where(
                FuelTransaction.org_id == org_id,
                FuelTransaction.entity_id == claim.entity_id,
                FuelTransaction.country == claim.refund_country,
                FuelTransaction.period.in_(months),
            )
        )
    )

    # Group by (resolved invoice ref or UNMATCHED, product_group) — C1: one
    # row per (invoice, product code), never an "ALL:" country aggregate.
    groups: dict[tuple[str, str], dict[str, object]] = {}
    for txn in txns:
        matched = await resolve_invoice_ref(
            db,
            org_id,
            entity_id=claim.entity_id,
            supplier=txn.supplier,
            refund_country=claim.refund_country,
            invoice_ref=txn.invoice_ref,
        )
        ref = matched.invoice_number if matched is not None else UNMATCHED
        invoice_id = matched.invoice_id if matched is not None else None
        key = (ref, txn.product_group)
        bucket = groups.setdefault(
            key, {"invoice_id": invoice_id, "net_eur": Decimal("0"), "vat_eur": Decimal("0")}
        )
        bucket["net_eur"] = bucket["net_eur"] + txn.net_eur  # type: ignore[operator]
        bucket["vat_eur"] = bucket["vat_eur"] + txn.vat_eur  # type: ignore[operator]

    # Replace only the UNFROZEN lines — see docstring "frozen_at IS NULL
    # scoping". A frozen line (future G2.5) is never touched here.
    await db.execute(
        delete(VatRefundClaimLine).where(
            VatRefundClaimLine.org_id == org_id,
            VatRefundClaimLine.claim_id == claim.id,
            VatRefundClaimLine.frozen_at.is_(None),
        )
    )

    lines: list[VatRefundClaimLine] = []
    for (ref, product_group), bucket in sorted(groups.items(), key=lambda kv: kv[0]):
        line = VatRefundClaimLine(
            org_id=org_id,
            claim_id=claim.id,
            invoice_ref=ref,
            invoice_id=bucket["invoice_id"],
            product_group=product_group,
            net_eur=q2(bucket["net_eur"]),  # type: ignore[arg-type]
            vat_eur=q2(bucket["vat_eur"]),  # type: ignore[arg-type]
        )
        db.add(line)
        lines.append(line)

    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_CLAIM_LINES_BUILD,
        target_type="vat_refund_claim",
        target_id=claim.id,
        meta={"line_count": len(lines), "transaction_count": len(txns)},
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
    return lines
