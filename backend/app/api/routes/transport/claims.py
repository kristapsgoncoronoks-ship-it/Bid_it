"""Transport routes slice 1 — the VAT refund claim lifecycle (WO-76).

Thin controllers ONLY (engineering-rules §3): parse → structural permission
gate → call the already-gated service → shape the response. Every refusal a
handler surfaces is raised BY THE SERVICE as an `app.core.errors.AppError`
and rendered by the one `app.main` handler as `{"detail", "code"}` — the
routes map nothing themselves, so the wire vocabulary (`module_not_enabled`,
`claim_not_found`, `period_not_ended`, `below_minimum`, `customer_not_active`,
`country_not_activated`, `unresolved_invoice_refs`, `duplicate_invoice_lock`,
`invoice_document_missing`, `claim_not_draft`, `claim_not_locked`, …) cannot
drift from the service layer (master-context §4.20).

Authorization (ADR-0024, structural): router-level `VAT_READ`; the stricter
verbs override per-route — `VAT_WRITE` on create/build-lines (booking claim
data), `VAT_SUBMIT` on submit/withdraw (acquiring/releasing invoice locks is
the consequential act the WO-49 permission split isolates: an ACCOUNTANT
holds VAT_WRITE but not VAT_SUBMIT, mirroring ISSUED_WRITE vs ISSUED_SEND).
The module entitlement (`transport`, default off) is enforced INSIDE every
service entry point (`module_not_enabled`, 403) — ADR-P3 rule 3 with the
WO-49 defense-in-depth convention that no transport service trusts its
caller to have checked it.

Mutating handlers commit AFTER the service returns (the `payment_runs`
pattern); the services audit in the same transaction (§4.16), so a refused
submit rolls back locks, freeze, status flip AND the never-warranted audit
row together — the D5 nothing-mutated guarantee holds over HTTP. GET
handlers never commit: `submission_checklist`'s idempotent default-rule
seeding is flushed but discarded with the request session, keeping reads
observably side-effect-free (§4.19 — reading the checklist or stage changes
NOTHING about the claim).

`lock.submit_claim`'s `today` parameter is a TEST SEAM and is deliberately
NOT exposed on the wire — a client that could post its own clock could
bypass the R7 period-end gate. The route always lets the service default to
the real date.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.schemas.transport_claim import (
    ChecklistItemOut,
    ClaimCreateIn,
    ClaimLineOut,
    ClaimOut,
    ClaimSubmitIn,
    StageOut,
)
from app.services.transport import checklist as checklist_svc
from app.services.transport import claim as claim_svc
from app.services.transport import claim_lines as claim_lines_svc
from app.services.transport import lock as lock_svc
from app.services.transport import status as status_svc

# Structural authorization (ADR-0024): router-level VAT_READ; the mutating
# routes declare the stricter VAT_WRITE / VAT_SUBMIT per-route below.
router = APIRouter(
    prefix="/transport/claims",
    tags=["transport"],
    dependencies=[Depends(require_perm(authz.Permission.VAT_READ))],
)
_WRITE = [Depends(require_perm(authz.Permission.VAT_WRITE))]
_SUBMIT = [Depends(require_perm(authz.Permission.VAT_SUBMIT))]


def _claim_out(claim: VatRefundClaim) -> ClaimOut:
    return ClaimOut(
        id=claim.id,
        entity_id=claim.entity_id,
        refund_country=claim.refund_country,
        ref_period=claim.ref_period,
        status=claim.status,
        status_code=claim.status_code,
        status_note=claim.status_note,
        decision_date=claim.decision_date,
        action_deadline=claim.action_deadline,
        submitted_date=claim.submitted_date,
        approved_date=claim.approved_date,
        paid_date=claim.paid_date,
        paid_amount=claim.paid_amount,
        vat_eur=claim.vat_eur,
        vat_local=claim.vat_local,
        currency=claim.currency,
        fee_pct=claim.fee_pct,
        fee_min=claim.fee_min,
        fee_eur=claim.fee_eur,
        created_at=claim.created_at,
    )


def _line_out(line: VatRefundClaimLine) -> ClaimLineOut:
    return ClaimLineOut(
        id=line.id,
        claim_id=line.claim_id,
        invoice_ref=line.invoice_ref,
        vat_id=line.vat_id,
        invoice_id=line.invoice_id,
        goods_code=line.goods_code,
        product_group=line.product_group,
        net_eur=line.net_eur,
        vat_eur=line.vat_eur,
        net_local=line.net_local,
        vat_local=line.vat_local,
        currency=line.currency,
        frozen_at=line.frozen_at,
    )


@router.get("", response_model=list[ClaimOut])
async def list_claims(current: CurrentUser, db: DbSession):
    rows = await claim_svc.list_claims(db, current.org_id)
    return [_claim_out(c) for c in rows]


@router.post("", response_model=ClaimOut, dependencies=_WRITE)
async def create_claim(body: ClaimCreateIn, current: CurrentUser, db: DbSession):
    """Get-or-create on the R1 grain `(entity, refund_country, ref_period)`.
    Deliberately 200, not 201: the service is idempotent by design ("same
    key upserts, never duplicates") and does not report whether it created —
    a 201 on the second identical call would be a lie on the wire."""
    claim = await claim_svc.get_or_create_claim(
        db,
        current.org_id,
        entity_id=body.entity_id,
        refund_country=body.refund_country,
        ref_period=body.ref_period,
    )
    await db.commit()
    return _claim_out(claim)


@router.get("/{claim_id}", response_model=ClaimOut)
async def get_claim(claim_id: str, current: CurrentUser, db: DbSession):
    claim = await claim_svc.get_claim(db, current.org_id, claim_id)
    return _claim_out(claim)


@router.get("/{claim_id}/lines", response_model=list[ClaimLineOut])
async def list_claim_lines(claim_id: str, current: CurrentUser, db: DbSession):
    """The claim's materialized lines — a draft's live rebuild, or exactly
    what was frozen at submission (`frozen_at` set)."""
    rows = await claim_lines_svc.list_claim_lines(db, current.org_id, claim_id)
    return [_line_out(ln) for ln in rows]


@router.post("/{claim_id}/lines", response_model=list[ClaimLineOut], dependencies=_WRITE)
async def build_claim_lines(claim_id: str, current: CurrentUser, db: DbSession):
    """(Re)materialize the draft claim's lines from its fuel transactions
    (R2 grain, R16 resolution, R15 waiver exclusion — all in the service)."""
    rows = await claim_lines_svc.build_claim_lines(db, current.org_id, claim_id)
    await db.commit()
    return [_line_out(ln) for ln in rows]


@router.get("/{claim_id}/checklist", response_model=list[ChecklistItemOut])
async def get_checklist(claim_id: str, current: CurrentUser, db: DbSession):
    """The advisory submission checklist (R45) — read-only on the wire: no
    commit, so even the evaluator's idempotent default-rule seeding is
    discarded with the request session (see module docstring)."""
    items = await checklist_svc.submission_checklist(db, current.org_id, claim_id)
    return [
        ChecklistItemOut(key=i.key, label=i.label, scope=i.scope, ok=i.ok, reason=i.reason)
        for i in items
    ]


@router.get("/{claim_id}/stage", response_model=StageOut)
async def get_stage(claim_id: str, current: CurrentUser, db: DbSession):
    """The system-derived pre-submission stage 1A/1B/1C/1E (R17) — a
    read-only preview for a draft claim; refused (409 `claim_not_draft`)
    once the claim leaves `draft`."""
    stage = await status_svc.derive_stage(db, current.org_id, claim_id)
    return StageOut(stage=stage)


@router.post("/{claim_id}/submit", response_model=ClaimOut, dependencies=_SUBMIT)
async def submit_claim(claim_id: str, body: ClaimSubmitIn, current: CurrentUser, db: DbSession):
    """The D5 gate chain (R7 → R8 → R44 → R3 → R6 → R15 stamp → R10 →
    freeze → lock → status "2"), entirely in `lock.submit_claim`. A refusal
    at ANY gate reaches the client as 409/422 with the service's stable
    `code` and mutates nothing (rolled back with the uncommitted session)."""
    claim = await lock_svc.submit_claim(
        db,
        current.org_id,
        claim_id=claim_id,
        invoices=[(i.supplier, i.invoice_ref, i.fuel_transaction_id) for i in body.invoices],
        override_minimum=body.override_minimum,
    )
    await db.commit()
    return _claim_out(claim)


@router.post("/{claim_id}/withdraw", response_model=ClaimOut, dependencies=_SUBMIT)
async def withdraw_claim(claim_id: str, current: CurrentUser, db: DbSession):
    """The ONLY lock-releasing transition (R5) — gated by the same
    VAT_SUBMIT permission as submit: releasing invoice locks re-opens the
    invoices to other claims, the mirror image of the consequential act."""
    claim = await lock_svc.withdraw_claim(db, current.org_id, claim_id)
    await db.commit()
    return _claim_out(claim)
