"""The one-invoice-one-submission lock: transactional acquisition + withdraw-only
release (G2.2; `BA_fleet_fuel.md` section 3.C5/C7, R4/R5).

WO-49 shipped the claim grain and WO-50 shipped the typed transaction line;
this module is the primitive every future gate (checklist, period-end,
deadline, minimum-amount — all G2.6) builds on top of: once an invoice is
locked, a caller can trust that fact is durable and race-proof. WO-56 (G2.6
slice 1) wires the first two REAL legal gates into `submit_claim` itself —
the hard period-end gate (R7, `deadline.period_ended`) and the Art. 17
minimum-amount gate (R8, `minimum.below_minimum`), in the SAME submission-
gate order Fleet Fuel's own `set_status_code` used (D5: checklist ->
period-end -> minimum -> then the lock/freeze machinery) — a below-minimum
or not-yet-ended claim never freezes or locks at all.

`submit_claim` is a MINIMAL STUB status transition (draft -> submitted) that
exists to prove the locking primitive — not the future G2.7 status machine.
It acquires one lock row per invoice via a plain ORM INSERT (`db.add()` +
`flush()`, never an `on_conflict_do_nothing` upsert) in the SAME flush as the
claim's `status` mutation: a lost race on ANY lock raises `IntegrityError` at
`flush()`, still inside the caller's open transaction, so the caller's
`rollback()` discards the failed lock row(s) AND the status flip together —
nothing partially applied (`BEGIN IMMEDIATE` on Fleet Fuel's SQLite; on this
stack, the same guarantee comes from never committing between the lock
inserts and the status update).

`withdraw_claim` is the ONLY function in this codebase that deletes a
`VatClaimedInvoice` row (R5 — "only an explicit withdraw releases locks;
rejection/confiscation/appeal keep them"). Since this order deliberately does
NOT build `approve`/`reject`/`pay` transitions (the full state machine is
G2.7, out of scope), there is currently no OTHER code path anywhere in
`app/services/transport/` that issues a DELETE against `vat_claimed_invoices`
— `tests/transport/test_g2_2_lock.py::
test_g2_2_withdraw_claim_is_the_only_function_that_deletes_a_lock_row` proves
this structurally (grep-based), and `test_g2_2_directly_flipping_claim_
status_never_cascades_a_lock_release` proves it behaviorally (mutating
`claim.status` directly on the ORM object never removes a lock row — there is
no trigger, no `ondelete` cascade keyed on a status value, only on row
deletion, which no other code path performs). When G2.7 later adds real
`approve`/`reject`/`pay` transitions, each one must positively NOT call
`withdraw_claim` — this module's test suite is the regression net that a
future PR breaking that would trip.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionError, ValidationError
from app.models.transport.lock import VatClaimedInvoice
from app.models.transport.vat_claim import VatRefundClaim
from app.services import audit, modules
from app.services.transport.deadline import period_ended
from app.services.transport.freeze import freeze_claim_lines, preview_vat_base
from app.services.transport.minimum import below_minimum, min_for

# R5 — the set of claim statuses that currently hold locks. withdraw_claim
# refuses any OTHER status (draft never held any; rejected/approved-then-
# rejected-in-a-future-state-machine etc. all keep them per R5 and simply
# aren't reachable via withdraw_claim either way in this order's scope).
_LOCK_HOLDING_STATUSES = ("submitted", "approved", "paid")


async def _get_claim(db: AsyncSession, org_id: str, claim_id: str) -> VatRefundClaim:
    """Org-scoped lookup — a cross-tenant claim id is indistinguishable from an
    unknown one (master-context §4.4: opaque 404, never 403)."""
    claim = await db.scalar(
        select(VatRefundClaim).where(VatRefundClaim.id == claim_id, VatRefundClaim.org_id == org_id)
    )
    if claim is None:
        raise NotFoundError("Claim not found", code="claim_not_found")
    return claim


async def submit_claim(
    db: AsyncSession,
    org_id: str,
    *,
    claim_id: str,
    invoices: list[tuple[str, str, str]],
    override_minimum: bool = False,
    today: date | None = None,
) -> VatRefundClaim:
    """Gate (period-end, R7; minimum-amount, R8), freeze the claim's lines
    (G2.5), acquire one lock per invoice, and flip the claim `draft` ->
    `submitted`, ALL in the same flush (see module docstring for the
    atomicity argument; `app.services.transport.freeze`'s own docstring for
    why freezing belongs inside this same transaction — "the linchpin",
    `ARCH_plan.md`'s own word for G2.5).

    Gate order (D5, verbatim): draft-status -> non-empty invoice set ->
    period-end (R7) -> Art. 17 minimum (R8) -> freeze -> lock -> status flip.
    A claim that fails EITHER gate never freezes or locks at all — nothing
    is mutated before the LAST gate passes.

    `override_minimum=True` bypasses the below-minimum refusal (R8's
    "admin override allowed... recorded in `status_note`") — see
    `app.services.transport.minimum`'s module docstring for why this is a
    plain boolean here, not yet a permission check (no route exists to
    enforce one). `today` is a test seam (defaults to `date.today()` inside
    `deadline.period_ended`) so period-end can be asserted deterministically
    without patching the clock.

    `invoices` is a plain list of `(supplier, invoice_ref, fuel_transaction_id)`
    tuples — still never derived from `vat_claim_lines` (a caller-supplied
    list is what THIS lock primitive keys on; a future slice of G2.6 is what
    will derive it from the now-frozen lines automatically). `entity_id`/
    `refund_country` for every lock row are read off the CLAIM, never
    re-supplied per invoice — the claim's grain already fixes those two
    fields, so letting them vary per invoice would let a caller lock an
    invoice under the wrong entity/country by mistake.

    Gated on the `transport` module entitlement FIRST, identical pattern to
    `claim.get_or_create_claim` / `fuel_ingest.ingest_transaction`: `modules.
    is_enabled` (a bool), never `modules.require_enabled` (which raises
    `fastapi.HTTPException` — the wrong shape for a service, which must
    signal via `app.core.errors.AppError`).
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    claim = await _get_claim(db, org_id, claim_id)

    if claim.status != "draft":
        raise ConflictError(
            f"Claim is '{claim.status}', not 'draft' — cannot submit", code="claim_not_draft"
        )
    if not invoices:
        raise ValidationError(
            "Cannot submit a claim with an empty invoice set", code="empty_claim_set"
        )

    # R7 — hard period-end gate. A claim period that has not fully closed
    # cannot be filed at all; checked BEFORE anything else mutates.
    if not period_ended(claim.ref_period, today=today):
        raise ConflictError(
            f"The '{claim.ref_period}' period has not ended yet — cannot submit",
            code="period_not_ended",
        )

    # R8 — Art. 17 minimum, compared in the CORRECT currency for
    # `claim.refund_country`. Previewed BEFORE freezing (D5's own gate
    # order): a below-minimum claim never freezes or locks, unless
    # explicitly overridden (recorded in `status_note`, never silent).
    preview_eur, preview_local, _preview_currency = await preview_vat_base(db, org_id, claim.id)
    if below_minimum(
        country=claim.refund_country,
        ref_period=claim.ref_period,
        vat_eur=preview_eur,
        vat_local=preview_local,
    ):
        if not override_minimum:
            raise ConflictError(
                f"Claim VAT amount is below the Art. 17 minimum for {claim.refund_country}",
                code="below_minimum",
            )
        currency, threshold, basis = min_for(
            claim.refund_country, is_annual=claim.ref_period.endswith("YEAR")
        )
        compared = preview_local if basis == "local" else preview_eur
        note = (
            f"Art. 17 minimum override: {compared} {currency} < threshold "
            f"{threshold} {currency} (basis={basis})"
        )
        claim.status_note = f"{claim.status_note}\n{note}" if claim.status_note else note

    # G2.5 — freeze the claim's lines + VAT base BEFORE flipping status, in
    # the same flush as the lock inserts below: a lost lock race rolls back
    # the freeze too (nothing partially applied), and a successful
    # submission never leaves locks acquired against an unfrozen claim.
    frozen_line_count = await freeze_claim_lines(db, org_id, claim)

    for supplier, invoice_ref, fuel_transaction_id in invoices:
        db.add(
            VatClaimedInvoice(
                org_id=org_id,
                claim_id=claim.id,
                entity_id=claim.entity_id,
                refund_country=claim.refund_country,
                supplier=supplier,
                invoice_ref=invoice_ref,
                fuel_transaction_id=fuel_transaction_id,
            )
        )
    claim.status = "submitted"
    # A lost race on ANY lock row's UNIQUE constraint raises IntegrityError
    # HERE — still inside the caller's open transaction. The caller's
    # rollback() then discards every lock row added above AND the status
    # flip together; nothing partially applied.
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_CLAIM_SUBMIT,
        target_type="vat_refund_claim",
        target_id=claim.id,
        meta={"invoice_count": len(invoices), "frozen_line_count": frozen_line_count},
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
    return claim


async def withdraw_claim(db: AsyncSession, org_id: str, claim_id: str) -> VatRefundClaim:
    """The ONLY function that deletes a `VatClaimedInvoice` row (R5). Deletes
    every lock row this claim currently holds and sets `status='withdrawn'` —
    after which the freed invoice key can be locked by a DIFFERENT claim.

    Refuses (409, `code="claim_not_locked"`) unless the claim is currently in
    a lock-holding status (`submitted`/`approved`/`paid`) — R5's "only an
    explicit withdraw releases; rejection/confiscation/appeal keep them" is
    modeled here by there being no OTHER function that could reach a locking
    state and shed its locks, since no reject/approve/pay transition exists
    yet (see module docstring).
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    claim = await _get_claim(db, org_id, claim_id)

    if claim.status not in _LOCK_HOLDING_STATUSES:
        raise ConflictError(
            f"Claim is '{claim.status}' and holds no locks — nothing to withdraw",
            code="claim_not_locked",
        )

    await db.execute(
        delete(VatClaimedInvoice).where(
            VatClaimedInvoice.org_id == org_id, VatClaimedInvoice.claim_id == claim.id
        )
    )
    claim.status = "withdrawn"
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_CLAIM_WITHDRAW,
        target_type="vat_refund_claim",
        target_id=claim.id,
        meta=None,
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
    return claim
