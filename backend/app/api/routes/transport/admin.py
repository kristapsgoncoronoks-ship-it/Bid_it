"""Transport routes slice 2 — the ORG-LEVEL admin/configuration surfaces
(WO-77): the status-code vocabulary (WO-59), checklist-rule administration
(WO-60/R45), receipt-control cadences + the persisted control grid
(WO-72/G3.5), note→invoice-ref overrides (WO-52/R16) and the engine tie-out
expectations (WO-66 regime 2 / R25).

Thin controllers ONLY (engineering-rules §3), exactly the WO-76 pattern:
parse → structural permission gate → call the already-gated service → shape
the response. Every refusal is raised BY THE SERVICE as an
`app.core.errors.AppError` and rendered by the one `app.main` handler as
`{"detail", "code"}` — the routes map nothing themselves, so the wire
vocabulary (`module_not_enabled`, `invalid_cadence`,
`checklist_rule_not_found`, `receipt_control_not_found`, `invalid_period`,
`invalid_currency`, `invalid_gross_tolerance`, `invalid_expected_lines`,
`tieout_expectation_not_found`, `override_note_is_synthetic`,
`override_target_not_registered`, `entity_not_found`, …) cannot drift from
the service layer (master-context §4.20).

AUTHORIZATION (ADR-0024, structural): router-level `VAT_READ`; every
CONFIG MUTATION overrides to `VAT_WRITE`. None of these verbs flips a claim
status or acquires/releases an invoice lock — they configure how claims are
BUILT and gated, which is the same tier as booking claim data. The one
claim-STATUS surface (`set_status_code`) deliberately lives in `claims.py`
under `VAT_SUBMIT`. No new permission member is introduced (WO-49 shipped
the transport vocabulary; §10 — no invented functionality).

WHAT IS DELIBERATELY NOT ROUTED HERE
------------------------------------
`receipt_control.run_receipt_control` and `orphan_transactions`: the control
RUN is the close's `run_control` stage and R60 is explicit that the close
never runs inline in a web request — this module serves the grid that run
PERSISTED. `invoice_match` has no override-REMOVAL function (R16's harvested
lifecycle is "de-register the target ⇒ CASCADE deletes the row"), so no
DELETE route is invented for it; the route surface mirrors the service
surface exactly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.models.transport.checklist_rule import VatChecklistRule
from app.models.transport.fee_rate import VatFeeRate
from app.models.transport.note_override import VatNoteInvoiceOverride
from app.models.transport.receipt_control import VatReceiptControl, VatSupplierCadence
from app.models.transport.tie_out import FuelTieOutExpectation
from app.schemas.transport_admin import (
    CadenceOut,
    CadenceSetIn,
    ChecklistRuleActiveIn,
    ChecklistRuleOut,
    ControlOverrideIn,
    FeeRateOut,
    FeeRateSetIn,
    NoteOverrideOut,
    NoteOverrideSetIn,
    ReceiptControlOut,
    RemovedOut,
    StatusCodesOut,
    TieOutExpectationOut,
    TieOutExpectationSetIn,
)
from app.services.transport import checklist as checklist_svc
from app.services.transport import fee as fee_svc
from app.services.transport import invoice_match as invoice_match_svc
from app.services.transport import receipt_control as receipt_control_svc
from app.services.transport import status as status_svc
from app.services.transport import tie_out as tie_out_svc

# Structural authorization (ADR-0024): router-level VAT_READ; the config
# mutations declare the stricter VAT_WRITE per-route below.
router = APIRouter(
    prefix="/transport",
    tags=["transport"],
    dependencies=[Depends(require_perm(authz.Permission.VAT_READ))],
)
_WRITE = [Depends(require_perm(authz.Permission.VAT_WRITE))]


# --------------------------------------------------------------------------- #
# Status-code vocabulary (WO-59, R17)
# --------------------------------------------------------------------------- #


@router.get("/status-codes", response_model=StatusCodesOut)
async def list_status_codes(current: CurrentUser, db: DbSession):
    """The claim status-code vocabulary: the system-derived AUTO codes
    (1A/1B/1C/1E — never settable, R17) and the MANUAL workflow codes
    `POST /transport/claims/{id}/status-code` accepts. This codebase has no
    label mapping (WO-77 decision 5) — the codes ARE the vocabulary."""
    auto, manual = await status_svc.list_status_codes(db, current.org_id)
    return StatusCodesOut(auto=list(auto), manual=list(manual))


# --------------------------------------------------------------------------- #
# Checklist rules (WO-60, R45)
# --------------------------------------------------------------------------- #


def _rule_out(row: VatChecklistRule) -> ChecklistRuleOut:
    return ChecklistRuleOut(
        id=row.id,
        key=row.key,
        label=row.label,
        scope=row.scope,
        check_type=row.check_type,
        reference=row.reference,
        active=row.active,
        sort=row.sort,
    )


@router.get("/checklist-rules", response_model=list[ChecklistRuleOut])
async def list_checklist_rules(current: CurrentUser, db: DbSession):
    """The adjustable submission-checklist rules as DATA (R45) — the rows
    themselves, as opposed to the claim-scoped checklist GET, which returns
    their EVALUATION against one claim."""
    rows = await checklist_svc.list_rules(db, current.org_id)
    return [_rule_out(r) for r in rows]


@router.post("/checklist-rules/seed", response_model=list[ChecklistRuleOut], dependencies=_WRITE)
async def seed_checklist_rules(current: CurrentUser, db: DbSession):
    """Persist the harvested `DEFAULT_RULES` (idempotent — returns only the
    NEWLY created rows, and a repeat call creates and audits nothing).

    This route exists because the rules only PERSIST when a COMMITTING caller
    runs the seed: the claim checklist GET deliberately never commits (§4.19
    — reading a claim's checklist must change nothing), so without this
    surface the rule table would stay empty and `set_active` could never find
    a row to toggle. It exposes the existing service function; it invents no
    rule."""
    created = await checklist_svc.seed_default_rules(db, current.org_id)
    await db.commit()
    return [_rule_out(r) for r in created]


@router.post("/checklist-rules/{key}/active", response_model=ChecklistRuleOut, dependencies=_WRITE)
async def set_checklist_rule_active(
    key: str, body: ChecklistRuleActiveIn, current: CurrentUser, db: DbSession
):
    """R45's own acceptance test over HTTP: deactivate a rule ⇒ it disappears
    from the gate. `set_active` is the ONLY writer of `active`; an unknown key
    is 404 `checklist_rule_not_found`, and a no-op audits nothing."""
    row = await checklist_svc.set_active(db, current.org_id, key, body.active)
    await db.commit()
    return _rule_out(row)


# --------------------------------------------------------------------------- #
# Receipt-control cadences + the persisted grid (WO-72, G3.5)
# --------------------------------------------------------------------------- #


def _cadence_out(row: VatSupplierCadence) -> CadenceOut:
    return CadenceOut(id=row.id, supplier=row.supplier, cadence=row.cadence)


@router.get("/cadences", response_model=list[CadenceOut])
async def list_cadences(current: CurrentUser, db: DbSession):
    """The ADMIN cadence assignments only. A supplier with no row still
    resolves through the harvested per-network defaults inside the service
    (`cadence_for`) — absence here is not "no cadence"."""
    rows = await receipt_control_svc.list_cadences(db, current.org_id)
    return [_cadence_out(r) for r in rows]


@router.put("/cadences/{supplier}", response_model=CadenceOut, dependencies=_WRITE)
async def set_cadence(supplier: str, body: CadenceSetIn, current: CurrentUser, db: DbSession):
    """Assign a supplier's invoicing cadence (beats the harvested default).
    A value outside the harvested closed set is 422 `invalid_cadence` — the
    service never guesses a cadence."""
    row = await receipt_control_svc.set_cadence(
        db, current.org_id, supplier=supplier, cadence=body.cadence
    )
    await db.commit()
    return _cadence_out(row)


@router.delete("/cadences/{supplier}", response_model=RemovedOut, dependencies=_WRITE)
async def remove_cadence(supplier: str, current: CurrentUser, db: DbSession):
    """Drop the admin assignment — the supplier falls back to the harvested
    default, or to no cadence at all (opt-in by absence). `removed=false` is
    the idempotent no-op, not a failure."""
    removed = await receipt_control_svc.remove_cadence(db, current.org_id, supplier=supplier)
    await db.commit()
    return RemovedOut(removed=removed)


def _control_out(row: VatReceiptControl) -> ReceiptControlOut:
    return ReceiptControlOut(
        id=row.id,
        entity_id=row.entity_id,
        supplier=row.supplier,
        period=row.period,
        slot=row.slot,
        country=row.country,
        status=row.status,
        txn_count=row.txn_count,
        waived=row.waived,
        note=row.note,
    )


@router.get("/receipt-controls", response_model=list[ReceiptControlOut])
async def list_receipt_controls(
    current: CurrentUser, db: DbSession, period: str = Query(min_length=7, max_length=7)
):
    """The persisted cadence × activity grid for one period (§3.J's board) —
    the RESULT the close's `run_control` stage wrote. ADVISORY throughout
    (§4.19): a `missing` slot is a chase-list row, never a gate. The control
    RUN itself is deliberately not routed (R60 — never inline in a request)."""
    rows = await receipt_control_svc.list_controls(db, current.org_id, period)
    return [_control_out(r) for r in rows]


@router.post(
    "/receipt-controls/{control_id}/override",
    response_model=ReceiptControlOut,
    dependencies=_WRITE,
)
async def set_control_override(
    control_id: str, body: ControlOverrideIn, current: CurrentUser, db: DbSession
):
    """§3.J item 4's manual override — the ONLY writer of `waived`/`note`,
    which a re-run preserves. `waived=true` means "stop chasing this slot": a
    worklist mute, NOT the claim-level legal waiver (R15) — it has zero effect
    on any claim, line, lock, freeze, checklist or ingest. A `null` field
    leaves that column unchanged; an unknown/cross-tenant id is an opaque 404
    `receipt_control_not_found` (§4.4)."""
    row = await receipt_control_svc.set_control_override(
        db, current.org_id, control_id, waived=body.waived, note=body.note
    )
    await db.commit()
    return _control_out(row)


# --------------------------------------------------------------------------- #
# Note→invoice-ref overrides (WO-52, R16/C4)
# --------------------------------------------------------------------------- #


def _override_out(row: VatNoteInvoiceOverride) -> NoteOverrideOut:
    return NoteOverrideOut(
        id=row.id,
        entity_id=row.entity_id,
        supplier=row.supplier,
        refund_country=row.refund_country,
        invoice_ref=row.invoice_ref,
        target_invoice_id=row.target_invoice_id,
    )


@router.get("/note-overrides", response_model=list[NoteOverrideOut])
async def list_note_overrides(current: CurrentUser, db: DbSession):
    """Every admin-curated note→invoice override in the org. Rows are shown
    as CURATED — a target since reassigned outside the pair still appears
    (that is the state an admin needs to see to fix it); the live
    re-validation that makes such a row stop RESOLVING lives in
    `resolve_invoice_ref` (C4), where it belongs."""
    rows = await invoice_match_svc.list_note_overrides(db, current.org_id)
    return [_override_out(r) for r in rows]


@router.put("/note-overrides", response_model=NoteOverrideOut, dependencies=_WRITE)
async def set_note_override(body: NoteOverrideSetIn, current: CurrentUser, db: DbSession):
    """Create or retarget the override for one (entity, supplier,
    refund_country, invoice_ref) key — idempotent on that key. An override
    changes only the invoice ASSOCIATION, never an amount (C4). Refuses 422
    `override_note_is_synthetic` (nothing real to override onto) and 422
    `override_target_not_registered` (the target is not a currently
    registered invoice for this supplier+country)."""
    row = await invoice_match_svc.set_note_override(
        db,
        current.org_id,
        entity_id=body.entity_id,
        supplier=body.supplier,
        refund_country=body.refund_country,
        invoice_ref=body.invoice_ref,
        target_invoice_id=body.target_invoice_id,
    )
    await db.commit()
    return _override_out(row)


# --------------------------------------------------------------------------- #
# Engine tie-out expectations (WO-66 regime 2, R25)
# --------------------------------------------------------------------------- #


def _expectation_out(row: FuelTieOutExpectation) -> TieOutExpectationOut:
    return TieOutExpectationOut(
        id=row.id,
        entity_id=row.entity_id,
        supplier=row.supplier,
        period=row.period,
        currency=row.currency,
        expected_lines=row.expected_lines,
        expected_gross_local=row.expected_gross_local,
        gross_local_tolerance=row.gross_local_tolerance,
        expected_net_eur=row.expected_net_eur,
        expected_gross_eur=row.expected_gross_eur,
        expected_diesel_litres=row.expected_diesel_litres,
    )


@router.get("/tie-out-expectations", response_model=list[TieOutExpectationOut])
async def list_tie_out_expectations(
    current: CurrentUser, db: DbSession, period: str = Query(min_length=7, max_length=7)
):
    """The figures a HUMAN typed from each supplier's invoice PDF for this
    period — the per-supplier opt-in training target the close ties out
    against (fail-OPEN by absence, fail-CLOSED once typed)."""
    rows = await tie_out_svc.list_expectations(db, current.org_id, period)
    return [_expectation_out(r) for r in rows]


@router.put("/tie-out-expectations", response_model=TieOutExpectationOut, dependencies=_WRITE)
async def set_tie_out_expectation(
    body: TieOutExpectationSetIn, current: CurrentUser, db: DbSession
):
    """UPSERT the (entity, supplier, period, currency) expectation — a typo
    must be correctable by simply retyping, and the audit trail keeps
    old→new. Money inputs cross the wire as Decimal-backed strings and the
    SERVICE is the single point that quantizes them (§4.9). Refuses 422 on a
    malformed period/currency, a negative `expected_lines`, or a tolerance
    outside the harvested [0.02, 0.05] band; 404 `entity_not_found` for an
    unknown or cross-tenant entity (§4.4)."""
    row = await tie_out_svc.set_expectation(
        db,
        current.org_id,
        entity_id=body.entity_id,
        supplier=body.supplier,
        period=body.period,
        currency=body.currency,
        expected_lines=body.expected_lines,
        expected_gross_local=body.expected_gross_local,
        gross_local_tolerance=body.gross_local_tolerance,
        expected_net_eur=body.expected_net_eur,
        expected_gross_eur=body.expected_gross_eur,
        expected_diesel_litres=body.expected_diesel_litres,
    )
    await db.commit()
    return _expectation_out(row)


@router.delete("/tie-out-expectations", status_code=status.HTTP_204_NO_CONTENT, dependencies=_WRITE)
async def remove_tie_out_expectation(
    current: CurrentUser,
    db: DbSession,
    entity_id: str = Query(),
    supplier: str = Query(),
    period: str = Query(min_length=7, max_length=7),
    currency: str = Query(min_length=3, max_length=3),
):
    """Delete a typed expectation by its natural key — the narrow un-halt for
    a mis-typed supplier (the close then treats that key as never-typed,
    fail-open by absence). An unknown or cross-tenant key is an opaque 404
    `tieout_expectation_not_found` (§4.4)."""
    await tie_out_svc.remove_expectation(
        db,
        current.org_id,
        entity_id=entity_id,
        supplier=supplier,
        period=period,
        currency=currency,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Contingency fee rates (R13 / C11 / the 2026-08-08 decision) ----------------
#
# `fee.set_rate` existed with NO HTTP surface, so the gate that refuses
# `fee_rate_not_configured` had no way to be opened except a Python shell: an
# org could not file a single claim through the product it had bought. These
# routes are that surface and nothing more — every rule (org rung carries no
# country, percentage/minimum validation, idempotent no-op, audited old→new)
# stays in the service, which remains the only writer.


def _fee_rate_out(row: VatFeeRate) -> FeeRateOut:
    return FeeRateOut(
        id=row.id,
        entity_id=row.entity_id,
        country=row.country,
        fee_pct=row.fee_pct,
        fee_min=row.fee_min,
    )


@router.get("/fee-rates", response_model=list[FeeRateOut])
async def list_fee_rates(current: CurrentUser, db: DbSession):
    """Every configured rung, MOST SPECIFIC FIRST — the order
    `resolve_fee_rate` walks, so a reader sees the chain as it resolves rather
    than in insertion order. An empty list means no claim can be submitted."""
    return [_fee_rate_out(r) for r in await fee_svc.list_rates(db, current.org_id)]


@router.put("/fee-rates", response_model=FeeRateOut, dependencies=_WRITE)
async def set_fee_rate(body: FeeRateSetIn, current: CurrentUser, db: DbSession):
    """Type the contingency rate for one rung. This pair decides what a client
    is billed, so the service audits every real change old→new in the same
    transaction; an identical repeat is a silent no-op."""
    row = await fee_svc.set_rate(
        db,
        current.org_id,
        entity_id=body.entity_id,
        country=body.country,
        fee_pct=body.fee_pct,
        fee_min=body.fee_min,
    )
    await db.commit()
    return _fee_rate_out(row)


@router.delete("/fee-rates", response_model=RemovedOut, dependencies=_WRITE)
async def remove_fee_rate(
    current: CurrentUser,
    db: DbSession,
    entity_id: str | None = Query(default=None),
    country: str | None = Query(default=None),
):
    """Drop one rung so resolution falls through to the next — and, if it was
    the last, to the refusal. Claims already filed keep their own frozen
    fee and are untouched. `removed=false` is the idempotent no-op."""
    removed = await fee_svc.remove_rate(db, current.org_id, entity_id=entity_id, country=country)
    await db.commit()
    return RemovedOut(removed=removed)
