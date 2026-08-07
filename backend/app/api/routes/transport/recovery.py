"""Transport routes slice 4 — the cash-recovery analytics surface (WO-81, G4.3).

One thin controller over `app.services.transport.recovery.recovery_dashboard`:
the first transport route that returns a PORTFOLIO figure rather than a claim,
a line or a transaction. Parse → structural permission gate → call the
already-gated service → shape the response (engineering-rules §3). The two
refusals a client can see (`module_not_enabled`, `invalid_year`) are raised BY
THE SERVICE as `app.core.errors.AppError` and rendered by the one `app.main`
handler as `{"detail", "code"}` — this module maps nothing, so the wire
vocabulary cannot drift from the service layer (master-context §4.20).

AUTHORIZATION (ADR-0024, structural): router-level `TRANSPORT_READ`, and this is
the route the permission was being reserved for. WO-79 gated the fuel-transaction
listing `VAT_READ` and recorded why: *"this surface is not analytics: these rows
ARE the evidence base of a claim … `TRANSPORT_READ` stays reserved for the
derived analytics/excise slices (`recovery.py`/`excise.py`/`overcharges.py`),
which have no backing service yet."* This module IS that derived analytics slice
— it returns no claim row, no line, no object id, only aggregates over data the
`VAT_READ` routes already serve in detail. Honouring the reservation is what
makes the two permissions mean something rather than being synonyms.

The choice changes no effective access today: `ROLE_PERMISSIONS` grants
`VAT_READ` and `TRANSPORT_READ` to exactly the same six roles and denies both to
APPROVER and EMPLOYEE — pinned by
`test_wo79_vat_read_and_transport_read_have_identical_role_coverage`, which now
guards two routes' assumptions. No permission member is added (§10).

READ-ONLY BY CONSTRUCTION: one GET, no commit, and nothing to commit — the
service performs no write of any kind. The stage evaluator it calls seeds
default checklist rules idempotently (flushed, discarded with the request
session), the same read-only posture `GET /transport/claims/{id}/checklist` has
carried since WO-76: reading the dashboard changes NOTHING about a claim (§4.19).

The service's `today` test seam is deliberately NOT exposed on the wire. A client
that could post its own clock could make a claim 20 days from the fatal 30-Sep
time-bar (CJEU C-294/11 *Elsacom*) report as comfortable — the same reasoning
that keeps `lock.submit_claim`'s `today` off the submit route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.transport_recovery import (
    RecoveryBucketOut,
    RecoveryDashboardOut,
    RecoveryExcludedOut,
)
from app.services.transport import recovery as recovery_svc

# Structural authorization (ADR-0024): router-level TRANSPORT_READ. The only
# route here is a read, so there is no stricter per-route override.
router = APIRouter(
    prefix="/transport/recovery-dashboard",
    tags=["transport"],
    dependencies=[Depends(require_perm(authz.Permission.TRANSPORT_READ))],
)


@router.get("", response_model=RecoveryDashboardOut)
async def get_recovery_dashboard(
    current: CurrentUser,
    db: DbSession,
    year: int = Query(description="The REFUND year — the claim's own ref_period year"),
):
    """Every claim of one refund year bucketed into the six harvested readiness
    states (`ready · deadline · missing · below · submitted · paid`), with the
    north-star euros beside them: recovered, awaiting, claimable, the deadline-
    risk count and the median days-to-refund.

    Basis: **NET EUR** — every VAT amount is a `vat_eur` sum and crosses the
    wire as an exact decimal STRING (§4.9). `overcharges_eur` is the SECOND
    cash stream (G4.5/R41): booked cash recovered from suppliers for contract
    breaches, also EUR, deliberately outside the VAT reconciliation. Nothing is summed across currencies (§4.14):
    a draft whose lines span currencies is excluded from the euros and counted
    in `currency_mismatch_claims`.

    A year with no claims is a 200 with six empty buckets and zeroes — never a
    404, never an exception. Only a year outside the four-digit range a
    `ref_period` can carry is refused (422 `invalid_year`).
    """
    dash = await recovery_svc.recovery_dashboard(db, current.org_id, year)
    return RecoveryDashboardOut(
        year=dash.year,
        currency=dash.currency,
        total_claims=dash.total_claims,
        buckets=[
            RecoveryBucketOut(state=b.state, claims=b.claims, vat_eur=b.vat_eur)
            for b in dash.buckets
        ],
        excluded=[RecoveryExcludedOut(reason=e.reason, claims=e.claims) for e in dash.excluded],
        recovered_eur=dash.recovered_eur,
        awaiting_eur=dash.awaiting_eur,
        claimable_eur=dash.claimable_eur,
        overcharges_eur=dash.overcharges_eur,
        deadline_risk_claims=dash.deadline_risk_claims,
        currency_mismatch_claims=dash.currency_mismatch_claims,
        median_days_to_refund=dash.median_days_to_refund,
        days_to_refund_sample=dash.days_to_refund_sample,
    )
