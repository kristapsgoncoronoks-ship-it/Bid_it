"""The one-invoice-one-submission lock: transactional acquisition + withdraw-only
release (G2.2; `BA_fleet_fuel.md` section 3.C5/C7, R4/R5).

WO-49 shipped the claim grain and WO-50 shipped the typed transaction line;
this module is the primitive every future gate (checklist, period-end,
deadline, minimum-amount — all G2.6) builds on top of: once an invoice is
locked, a caller can trust that fact is durable and race-proof. WO-56 (G2.6
slice 1) wired the first two REAL legal gates into `submit_claim` itself —
the hard period-end gate (R7, `deadline.period_ended`) and the Art. 17
minimum-amount gate (R8, `minimum.below_minimum`). WO-57 (G2.6 slice 2)
adds R6 — the annual mop-up / quarterly duplicate-block
(`_apply_annual_mop_up_or_duplicate_block`) — in the SAME submission-gate
order Fleet Fuel's own `set_status_code` used (D5: checklist -> period-end
-> minimum -> the duplicate/lock machinery) — a below-minimum, not-yet-
ended, or duplicate-overlapping claim never freezes or locks at all.
WO-58 (G2.6 slice 3) adds R10 (the document-presence gate,
`document_gate.enforce_document_presence` — every real, resolved claim
line needs a vaulted document) and R15 (receipt-control waivers,
`waiver.waived_suppliers` — a genuinely-uninvoiced supplier's synthetic
lines are already excluded by `claim_lines.build_claim_lines` before this
function ever runs; this module only stamps the waiver's USE into
`status_note` on submission). Both run AFTER the R6 mop-up/duplicate-block
gate and BEFORE the freeze — matching D5's own ordering ("...then
`set_status(engine)` which applies synthetic/duplicate/document gates").
WO-75 completes that engine-gate group with its FIRST member: the R3
synthetic gate (`claim_gates.enforce_no_synthetic_lines` — a claim whose
materialized lines include ANY unresolved/synthetic ref never freezes or
locks), positioned at the head of the group per C9's own internal order
("dropped from `invs` before the `bad` gate, `claim_set`, locks, doc-gate
and the frozen VAT base": bad/synthetic -> claim_set+locks (R6) -> doc-gate
(R10) -> freeze) — closing the gap WO-74's design decision 8 recorded.

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
from app.services.transport.claim_gates import enforce_no_synthetic_lines
from app.services.transport.customer_lifecycle import enforce_activation
from app.services.transport.deadline import period_ended
from app.services.transport.document_gate import enforce_document_presence
from app.services.transport.freeze import freeze_claim_lines, preview_vat_base
from app.services.transport.minimum import below_minimum, min_for
from app.services.transport.waiver import waived_suppliers

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


async def _existing_locks_by_key(
    db: AsyncSession, org_id: str, entity_id: str, refund_country: str
) -> dict[tuple[str, str], VatClaimedInvoice]:
    """Every CURRENTLY-locked `(supplier, invoice_ref)` for this
    (entity, refund_country) — across EVERY claim, not just the one being
    submitted (R6 needs to see locks other claims already hold)."""
    rows = await db.scalars(
        select(VatClaimedInvoice).where(
            VatClaimedInvoice.org_id == org_id,
            VatClaimedInvoice.entity_id == entity_id,
            VatClaimedInvoice.refund_country == refund_country,
        )
    )
    return {(row.supplier, row.invoice_ref): row for row in rows}


async def _apply_annual_mop_up_or_duplicate_block(
    db: AsyncSession,
    org_id: str,
    claim: VatRefundClaim,
    invoices: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """R6 (`BA_fleet_fuel.md` C6): an ANNUAL (`-YEAR`) claim is the MOP-UP —
    an invoice already locked by a QUARTERLY claim is silently EXCLUDED
    (`continue`, not a conflict); a QUARTERLY claim treats ANY existing lock
    on one of its invoices as a duplicate and BLOCKS THE WHOLE SUBMISSION.
    An invoice already locked by ANOTHER ANNUAL claim (a genuine annual-
    annual duplicate — not the mop-up case the harvested text describes) is
    also treated as a blocking duplicate, fail-closed, since the source text
    only ever describes excluding a QUARTER's lock.

    Returns the (possibly narrowed, for the annual case) list to actually
    lock. Raises `ConflictError` (`code="duplicate_invoice_lock"`) for a
    blocking duplicate, or `ValidationError`
    (`code="empty_claim_set"`) if an annual claim's mop-up set is empty
    after exclusion ("nothing to claim annually", C6 verbatim).
    """
    existing = await _existing_locks_by_key(db, org_id, claim.entity_id, claim.refund_country)
    if not existing:
        return invoices

    is_annual = claim.ref_period.endswith("YEAR")

    if not is_annual:
        duplicates = [key for key in {(s, r) for s, r, _ in invoices} if key in existing]
        if duplicates:
            names = ", ".join(f"{s}/{r}" for s, r in sorted(duplicates))
            raise ConflictError(
                f"Invoice(s) already locked by another claim: {names}",
                code="duplicate_invoice_lock",
            )
        return invoices

    to_lock: list[tuple[str, str, str]] = []
    for supplier, invoice_ref, fuel_transaction_id in invoices:
        existing_row = existing.get((supplier, invoice_ref))
        if existing_row is None:
            to_lock.append((supplier, invoice_ref, fuel_transaction_id))
            continue
        owner = await db.get(VatRefundClaim, existing_row.claim_id)
        if owner is not None and not owner.ref_period.endswith("YEAR"):
            continue  # mop-up: a quarter already claimed it — silently excluded
        raise ConflictError(
            f"Invoice {supplier}/{invoice_ref} is already locked by another annual claim",
            code="duplicate_invoice_lock",
        )

    if not to_lock:
        raise ValidationError(
            "Annual claim has nothing left to claim after excluding quarter-locked invoices",
            code="empty_claim_set",
        )
    return to_lock


async def submit_claim(
    db: AsyncSession,
    org_id: str,
    *,
    claim_id: str,
    invoices: list[tuple[str, str, str]],
    override_minimum: bool = False,
    today: date | None = None,
) -> VatRefundClaim:
    """Gate (period-end, R7; minimum-amount, R8; customer-lifecycle +
    per-country activation, R44; synthetic-line refusal, R3; annual mop-up /
    quarterly duplicate-block, R6; document-presence, R10), stamp any active waiver's
    use into `status_note` (R15), freeze the claim's lines (G2.5), acquire
    one lock per invoice, and flip the claim `draft` -> `submitted`, ALL in
    the same flush (see module docstring for the atomicity argument;
    `app.services.transport.freeze`'s own docstring for why freezing
    belongs inside this same transaction — "the linchpin", `ARCH_plan.md`'s
    own word for G2.5).

    Gate order (D5, verbatim): draft-status -> non-empty invoice set ->
    period-end (R7) -> Art. 17 minimum (R8) -> customer-lifecycle +
    per-country activation (R44, WO-73 — §3.E places the activation gates
    "layered on top" of `set_status`, i.e. at the ENTRY of D5's engine-gate
    group "...then `set_status(engine)`", so it sits after the minimum and
    before the engine gates proper) -> synthetic-line refusal (R3, WO-75 —
    the FIRST engine gate: D5 names "synthetic/duplicate/document" in that
    order, and C9's internal `set_status` sequence puts the `bad` gate
    before `claim_set`/locks/doc-gate) -> annual mop-up / quarterly
    duplicate-block (R6) -> waiver status_note stamp (R15) -> document-
    presence (R10) -> freeze -> lock -> status flip. A claim that fails ANY
    gate never freezes or locks at all — nothing is mutated before the LAST
    gate passes.

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

    # R44 (G2.11, WO-73) — the customer must be a live, contracted client
    # (`lifecycle status == 'active'`, F1: "every legal/claim gate keys off
    # 'active' — a prospect is ignored exactly like a pending customer")
    # AND the refund country separately activated (PoA received, §3.E's
    # second bullet). Fails CLOSED: an absent lifecycle/activation row
    # refuses. Positioned per §3.E "layered on top (set_status...)" — the
    # head of D5's engine-gate group, after the minimum, before R6.
    await enforce_activation(
        db, org_id, entity_id=claim.entity_id, refund_country=claim.refund_country
    )

    # R3 (WO-75) — the synthetic-line refusal, THE lock-gate consumer of the
    # one `claim_gates.is_synthetic()` predicate (C2: "a pack containing ANY
    # synthetic line CANNOT be filed"). First of D5's engine gates, per C9's
    # internal order (the `bad` gate runs before `claim_set`/locks/doc-gate/
    # freeze). Scans the claim's OWN materialized unfrozen lines — exactly
    # what the freeze below would stamp — so an UNMATCHED/INPUT/aggregate
    # line is refused while the claim is still `draft` and fixable, instead
    # of freezing+locking now and failing later in `claim_pack`.
    await enforce_no_synthetic_lines(db, org_id, claim.id)

    # R6 — annual mop-up / quarterly-overlap-blocks (C6). Narrows `invoices`
    # for an annual claim (excluding what a quarter already locked) or
    # blocks the whole submission outright for a quarterly claim overlapping
    # an existing lock. Runs BEFORE the freeze — a blocked/narrowed-to-empty
    # submission never freezes or locks anything.
    invoices = await _apply_annual_mop_up_or_duplicate_block(db, org_id, claim, invoices)

    # R15 — stamp every waiver currently active on this claim into
    # `status_note` (C9: "the waiver use is stamped into the claim's
    # status_note on submission"). The EXCLUSION itself already happened in
    # `claim_lines.build_claim_lines` (a waived supplier's transactions
    # never became a claim line in the first place); this is only the
    # audit-trail note.
    waived = await waived_suppliers(db, org_id, claim.id)
    if waived:
        names = ", ".join(sorted(waived))
        waiver_note = (
            f"Receipt-control waiver applied: {names} "
            f"(no registered invoice for {claim.refund_country})"
        )
        claim.status_note = (
            f"{claim.status_note}\n{waiver_note}" if claim.status_note else waiver_note
        )

    # R10 — document-presence gate. Every real, resolved claim line (never
    # an UNMATCHED one) needs >=1 vaulted document; checked against the
    # claim's own materialized lines, BEFORE the freeze — a claim missing a
    # document never freezes or locks.
    await enforce_document_presence(db, org_id, claim.id)

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
    # G2.7 (WO-59) — the workflow CODE mirrors the engine transition: "2"
    # (Submit) is the ONLY manual code this codebase ever stamps outside
    # `set_status_code` (which deliberately refuses to accept "2" at all —
    # see `status.py`'s module docstring), since submission is this gated
    # procedure, never a bare label write.
    claim.status_code = "2"
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
    every lock row this claim currently holds, sets `status='withdrawn'` and
    CLEARS the workflow `status_code` — after which the freed invoice key can
    be locked by a DIFFERENT claim.

    Refuses (409, `code="claim_not_locked"`) unless the claim is currently in
    a lock-holding status (`submitted`/`approved`/`paid`) — R5's "only an
    explicit withdraw releases; rejection/confiscation/appeal keep them" is
    modeled here by there being no OTHER function that could reach a locking
    state and shed its locks, since no reject/approve/pay transition exists
    yet (see module docstring).

    WHY THE CODE IS NULLED HERE (D7, WO-94)
    ---------------------------------------
    §3.D **D7** reads: *"Only `withdraw_claim` releases, and it also NULLs
    `status_code`."* Until WO-94 this function left the code populated, so
    every withdrawn claim carried the `"2"` `submit_claim` stamps (or whatever
    `status.set_status_code` last wrote) and read as *submitted* / *under
    appeal* beside its `withdrawn` engine status.

    The clear belongs HERE, at the one transition that owns the withdrawal,
    and not in `status.set_status_code`: that function is the MANUAL-code
    writer and already refuses a withdrawn claim (`claim_not_submitted`), so
    it can neither reach this state nor undo it. Both columns move in the same
    flush as the lock deletion, inside the caller's open transaction — a
    rollback discards the release, the status and the code together, exactly
    as it already did for the first two.
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
    old_code = claim.status_code
    claim.status = "withdrawn"
    # D7 (WO-94) — the withdrawal NULLs the workflow code. Same statement
    # group, same flush, same transaction as the status flip above.
    claim.status_code = None
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_CLAIM_WITHDRAW,
        target_type="vat_refund_claim",
        target_id=claim.id,
        # §4.16 — old->new for the value that changed, in the SAME transaction.
        # The field names are `status.set_status_code`'s, reused rather than
        # re-invented, so an audit consumer needs ONE shape for this column.
        meta={"old_status_code": old_code, "new_status_code": None},
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
    return claim
