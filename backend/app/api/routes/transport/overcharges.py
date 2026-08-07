"""Transport routes slice 5 — the supplier overcharge surface (WO-82, G4.5/R41).

`BA_fleet_fuel.md` §2.4's `/overcharges`: *"What does the supplier owe us for
breaching the contract?"* Three things over HTTP — the agreed contract TERMS an
admin curates, the read-only breach DETECTION over them, and the per-(supplier ×
period) claim-back LIFECYCLE that turns a detected euro into booked cash.

Thin controllers ONLY (engineering-rules §3), the WO-76/WO-77/WO-81 pattern:
parse → structural permission gate → call the already-gated service → shape the
response. Every refusal a client can see (`module_not_enabled`,
`invalid_period`, `invalid_country`, `invalid_product_group`,
`term_has_no_figure`, `invalid_term_rate`, `no_overcharge_detected`,
`overcharge_claim_not_found`, `invalid_overcharge_status`,
`overcharge_transition_invalid`, `recovered_amount_required`,
`recovered_amount_invalid`, `recovered_amount_not_applicable`, `invalid_year`)
is raised BY THE SERVICE as an `app.core.errors.AppError` and rendered by the
one `app.main` handler as `{"detail", "code"}` — this module maps nothing, so
the wire vocabulary cannot drift from the service layer (master-context §4.20).

AUTHORIZATION (ADR-0024, structural)
--------------------------------------
Router-level **`TRANSPORT_READ`**; the write verbs override to **`VAT_WRITE`**.

`TRANSPORT_READ` for the reads is the reservation `app/api/routes/transport/
fuel.py` recorded in as many words: *"`TRANSPORT_READ` stays reserved for the
derived analytics/excise slices (`recovery.py`/`excise.py`/`overcharges.py`)"* —
this module is named in that sentence, and WO-81 honoured the same reservation
for the dashboard.

`VAT_WRITE` for the mutations, per-route, the `admin.py` `_WRITE` pattern.
`VAT_WRITE` is this codebase's ONE transport write permission and already
governs every transport surface that is not a VAT-claim STATUS flip — cadences,
tie-out expectations, note overrides, checklist rules, the R44 customer
lifecycle — none of which is "VAT" either. `VAT_SUBMIT` is deliberately NOT
used: `authz.py`'s own comment reserves it for actions that acquire or release
an invoice LOCK on a VAT claim, and an overcharge claim-back touches no claim,
no lock and no VAT figure (R41: "read-only over the analytics"). **No permission
member is added** (§10).

The role sets already line up: every role holding `VAT_WRITE` (OWNER,
ADMINISTRATOR, FINANCE_MANAGER, ACCOUNTANT) also holds `TRANSPORT_READ`, so no
write route hides behind reads its holder lacks — while AUDITOR and READ_ONLY
get the analytics without the worklist verbs, which is exactly the difference
between the two permissions.

THE PRICE BASIS IS ON THE WIRE
--------------------------------
§3.G G1 requires the basis to be *"stated on any new report surface"*, and R53
requires this analysis's framing not to be flattened. Both ride the audit
response itself (`price_basis`, `legal_framing`) rather than living only in a
docstring, so no consumer can render the euro without them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.models.transport.contract_term import VatSupplierContractTerm
from app.models.transport.overcharge import VatOverchargeClaim
from app.schemas.transport_overcharge import (
    BreachOut,
    ContractAuditOut,
    ContractTermIn,
    ContractTermOut,
    OverchargeClaimAdvanceIn,
    OverchargeClaimOpenIn,
    OverchargeClaimOut,
    OverchargeTotalOut,
)
from app.services.transport import contract_audit as audit_svc
from app.services.transport import overcharge as overcharge_svc

# Structural authorization (ADR-0024): router-level TRANSPORT_READ; every
# mutation declares the stricter VAT_WRITE per-route below.
router = APIRouter(
    prefix="/transport",
    tags=["transport"],
    dependencies=[Depends(require_perm(authz.Permission.TRANSPORT_READ))],
)
_WRITE = [Depends(require_perm(authz.Permission.VAT_WRITE))]

# Every euro and every €/L rate this module serves is EUR — see the module
# docstring and `contract_audit.py`'s §4.14 section.
_EUR = "EUR"


def _term_out(row: VatSupplierContractTerm) -> ContractTermOut:
    return ContractTermOut(
        id=row.id,
        supplier=row.supplier,
        country=row.country,
        station_like=row.station_like,
        product_group=row.product_group,
        expected_discount_eur_l=row.expected_discount_eur_l,
        max_net_eur_l=row.max_net_eur_l,
        active=row.active,
    )


def _claim_out(row: VatOverchargeClaim) -> OverchargeClaimOut:
    return OverchargeClaimOut(
        id=row.id,
        supplier=row.supplier,
        period=row.period,
        status=row.status,
        detected_eur=row.detected_eur,
        lines_count=row.lines_count,
        recovered_eur=row.recovered_eur,
        note=row.note,
        currency=_EUR,
        created_at=row.created_at,
    )


# --------------------------------------------------------------------------- #
# Contract terms (the agreed commercial terms a supplier can breach)
# --------------------------------------------------------------------------- #


@router.get("/contract-terms", response_model=list[ContractTermOut])
async def list_contract_terms(
    current: CurrentUser,
    db: DbSession,
    supplier: str | None = Query(default=None, description="fuel-card / toll-network code"),
    country: str | None = Query(default=None, min_length=2, max_length=2),
    active_only: bool = Query(default=False),
):
    """The configured terms as DATA (`BA_fleet_fuel.md` §4.4's own grain), the
    R45 checklist-rule-admin shape. Only two term types exist — an expected
    discount in €/L and a NET price ceiling in €/L — and both cross the wire as
    exact decimal strings."""
    rows = await audit_svc.list_terms(
        db, current.org_id, supplier=supplier, country=country, active_only=active_only
    )
    return [_term_out(r) for r in rows]


@router.put("/contract-terms", response_model=ContractTermOut, dependencies=_WRITE)
async def set_contract_term(current: CurrentUser, db: DbSession, body: ContractTermIn):
    """Create or update the agreed term on its natural key `(supplier, country,
    station pattern, product group)` — the WO-77 tie-out-expectation shape (a
    PUT keyed by the body, not by an id).

    Idempotent: identical values return the row and audit nothing; a changed
    figure audits old→new in the same transaction (§4.16), because these €/L
    numbers determine a euro this platform then demands from a supplier.
    """
    row = await audit_svc.set_term(
        db,
        current.org_id,
        supplier=body.supplier,
        country=body.country,
        product_group=body.product_group,
        station_like=body.station_like,
        expected_discount_eur_l=body.expected_discount_eur_l,
        max_net_eur_l=body.max_net_eur_l,
        active=body.active,
    )
    await db.commit()
    await db.refresh(row)
    return _term_out(row)


@router.delete("/contract-terms", response_model=dict, dependencies=_WRITE)
async def remove_contract_term(
    current: CurrentUser,
    db: DbSession,
    supplier: str = Query(),
    country: str = Query(min_length=2, max_length=2),
    product_group: str = Query(),
    station_like: str = Query(default=""),
):
    """Delete a MIS-KEYED term. A contract that merely lapsed should be
    deactivated instead (`PUT` with `active=false`), so a past period can still
    be audited against the terms that applied to it — the service docstring
    carries the same warning. `{"removed": false}` when there was nothing to
    remove; a no-op audits nothing."""
    removed = await audit_svc.remove_term(
        db,
        current.org_id,
        supplier=supplier,
        country=country,
        product_group=product_group,
        station_like=station_like,
    )
    await db.commit()
    return {"removed": removed}


# --------------------------------------------------------------------------- #
# Detection (read-only)
# --------------------------------------------------------------------------- #


@router.get("/overcharges/audit", response_model=ContractAuditOut)
async def audit_contract_terms(
    current: CurrentUser,
    db: DbSession,
    period: str = Query(description="YYYY-MM — the accounting month of the fuel lines"),
    supplier: str | None = Query(default=None, description="fuel-card / toll-network code"),
):
    """R41's detection half — every validated fuel line of one accounting month
    checked against the agreed terms.

    Basis: **NET EUR/L, final — VAT excluded, rebates applied** (§3.G G1 / R49),
    carried on the response as `price_basis`. Currency: **EUR**, never a
    cross-currency sum (§4.14) — a line's own document currency rides beside it
    as provenance and is never totalled.

    READ-ONLY: no write, no audit event, no commit. Reading this changes nothing
    (R41 "read-only over the analytics"; §4.19). A month with no configured term
    returns 200 with an empty `breaches` list and `lines_without_terms` set —
    never a finding, because "we have not told the system what was agreed" is
    not "the supplier honoured the contract".
    """
    result = await audit_svc.audit(db, current.org_id, period=period, supplier=supplier)
    return ContractAuditOut(
        period=result.period,
        supplier=result.supplier,
        currency=result.currency,
        price_basis=result.price_basis,
        legal_framing=result.legal_framing,
        breaches=[
            BreachOut(
                fuel_transaction_id=b.fuel_transaction_id,
                entity_id=b.entity_id,
                supplier=b.supplier,
                country=b.country,
                station=b.station,
                product_group=b.product_group,
                txn_date=b.txn_date,
                qty=b.qty,
                document_currency=b.document_currency,
                eur_l_doc=b.eur_l_doc,
                eur_l_eff=b.eur_l_eff,
                flag=b.flag,
                agreed_eur_l=b.agreed_eur_l,
                actual_eur_l=b.actual_eur_l,
                gap_eur_l=b.gap_eur_l,
                recover_eur=b.recover_eur,
            )
            for b in result.breaches
        ],
        recover_eur=result.recover_eur,
        lines_audited=result.lines_audited,
        lines_without_terms=result.lines_without_terms,
        lines_skipped_zero_qty=result.lines_skipped_zero_qty,
    )


# --------------------------------------------------------------------------- #
# The claim-back worklist
# --------------------------------------------------------------------------- #


@router.get("/overcharges", response_model=list[OverchargeClaimOut])
async def list_overcharge_claims(
    current: CurrentUser,
    db: DbSession,
    period: str | None = Query(default=None, description="YYYY-MM"),
    supplier: str | None = Query(default=None),
    status: str | None = Query(default=None, description="one of the harvested claim-back states"),
):
    """The claim-back worklist, newest period first. A `status` outside the
    harvested six is refused 422 rather than silently returning nothing — an
    empty worklist and a typo must never look the same."""
    rows = await overcharge_svc.list_claims(
        db, current.org_id, period=period, supplier=supplier, status=status
    )
    return [_claim_out(r) for r in rows]


@router.get("/overcharges/total", response_model=OverchargeTotalOut)
async def overcharge_recovered_total(
    current: CurrentUser,
    db: DbSession,
    year: int | None = Query(default=None, description="filter on the claim-back period's year"),
):
    """§2.4's **booked-cash north star**: the euro actually recovered from
    suppliers, org-scoped, in EUR. This is the same figure
    `GET /transport/recovery-dashboard` reports as `overcharges_eur` — it calls
    this service, never a second query (R38's "never a forked query")."""
    total = await overcharge_svc.recovered_total(db, current.org_id, year=year)
    return OverchargeTotalOut(year=year, currency=_EUR, recovered_eur=total)


@router.get("/overcharges/{claim_id}", response_model=OverchargeClaimOut)
async def get_overcharge_claim(current: CurrentUser, db: DbSession, claim_id: str):
    """One claim-back. A cross-tenant id is indistinguishable from an unknown
    one: 404 `overcharge_claim_not_found`, never 403 (§4.4)."""
    row = await overcharge_svc.get_claim(db, current.org_id, claim_id)
    return _claim_out(row)


@router.post("/overcharges", response_model=OverchargeClaimOut, dependencies=_WRITE)
async def open_overcharge_claim(current: CurrentUser, db: DbSession, body: OverchargeClaimOpenIn):
    """Open the claim-back for one (supplier, period), FREEZING what detection
    found into `detected_eur` — the euro the demand letter quotes.

    Idempotent on the natural key: a second call returns the existing claim-back
    unchanged rather than re-snapshotting a figure already quoted to a supplier
    (the WO-76 `get_or_create_claim` posture — 200 on both calls, because the
    idempotency IS the contract).

    422 `no_overcharge_detected` when the audit found no breach: a claim-back at
    €0 would be a letter demanding nothing.
    """
    row = await overcharge_svc.open_claim(
        db, current.org_id, supplier=body.supplier, period=body.period
    )
    await db.commit()
    await db.refresh(row)
    return _claim_out(row)


@router.post(
    "/overcharges/{claim_id}/advance", response_model=OverchargeClaimOut, dependencies=_WRITE
)
async def advance_overcharge_claim(
    current: CurrentUser, db: DbSession, claim_id: str, body: OverchargeClaimAdvanceIn
):
    """Move a claim-back along the harvested chain `detected → packaged →
    claimed → recovered | rejected | written_off`.

    An illegal edge is 409 `overcharge_transition_invalid` with NOTHING mutated
    (no status, no amount, no note, no audit row). `recovered` requires the
    amount the supplier actually credited, bounded by the detected euro
    (no over-crediting, §4 invariant 13); no other target state accepts one.
    Every real move is audited old→new in the same transaction (§4.16).
    """
    row = await overcharge_svc.advance_claim(
        db,
        current.org_id,
        claim_id,
        to_status=body.to_status,
        recovered_eur=body.recovered_eur,
        note=body.note,
    )
    await db.commit()
    await db.refresh(row)
    return _claim_out(row)
