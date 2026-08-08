"""Transport routes slice 9 — the client claim-status portal (WO-93, G4.4/R39).

One thin controller over `app.services.transport.client_status.
client_claim_status`: parse -> structural permission gate -> call the
already-gated service -> shape the response (engineering-rules §3). The two
refusals a caller can see (`module_not_enabled`, `invalid_year`) are raised BY
THE SERVICE as `app.core.errors.AppError` and rendered by the one `app.main`
handler as `{"detail", "code"}` — this module maps nothing, so the wire
vocabulary cannot drift from the service layer (master-context §4.20).

AUTHORIZATION (ADR-0024, structural): router-level **`VAT_READ`**, and the
choice is semantic rather than a matter of access. WO-79 recorded the standing
reservation — *"`TRANSPORT_READ` stays reserved for the derived analytics/excise
slices"* — and WO-81 claimed it for `/recovery-dashboard` on the stated ground
that *"it returns no claim row, no line, no object id, only aggregates"*. This
route returns CLAIM ROWS: one per claim, with the claimant entity, the refunding
country and the period. It is the claims themselves, translated for their owner;
it is not portfolio analytics. Declaring it `VAT_READ` is what keeps the two
permissions from becoming synonyms by drift. **No permission member is added**
(master-context §10).

THE ROLE THIS SURFACE EXISTS FOR ALREADY HOLDS IT. `BA_fleet_fuel.md` §1.2
defines the reader as *"`user` | **The client.** Read-only base. Sees only
"open" read-only pages: `/value`, `/fees`, `/claim-status`, home"*, which is this
codebase's `Role.READ_ONLY` (stored `user_free`; `authz._LEGACY_ROLE` maps it).
`ROLE_PERMISSIONS[Role.READ_ONLY]` already holds `VAT_READ`, so the client can
reach this route without any change to the matrix — and `Role.APPROVER` /
`Role.EMPLOYEE`, which hold no VAT permission at all, still cannot.

READ-ONLY BY CONSTRUCTION: one GET, no commit, and nothing to commit — the
service performs no write of any kind. The stage evaluator it calls seeds
default checklist rules idempotently (flushed, discarded with the request
session), the same read-only posture `GET /transport/claims/{id}/checklist` has
carried since WO-76: opening the portal changes NOTHING about a claim (§4.19).

The service's `today` test seam is deliberately NOT exposed on the wire — a
caller that could post its own clock could change which stage its own claims
report, the same reasoning that keeps `lock.submit_claim`'s `today` off the
submit route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.transport_client_status import (
    ClientClaimOut,
    ClientClaimStatusOut,
    ClientStageOut,
)
from app.services.transport import client_status as client_status_svc

# Structural authorization (ADR-0024): router-level VAT_READ. The only route
# here is a read, so there is no stricter per-route override.
router = APIRouter(
    prefix="/transport/claim-status",
    tags=["transport"],
    dependencies=[Depends(require_perm(authz.Permission.VAT_READ))],
)


@router.get("", response_model=ClientClaimStatusOut)
async def get_client_claim_status(
    current: CurrentUser,
    db: DbSession,
    year: int = Query(description="The REFUND year — the claim's own ref_period year"),
):
    """R39 — *"where are my claims?"*, answered for the client who owns them.

    Every claim of one refund year lands in exactly one of the six harvested
    plain-language stages (`BA_fleet_fuel.md` §3.D: **prep · ready · filed ·
    awaiting · refunded**, plus **needs attention**), with the plain-language
    label and one explaining sentence carried beside it. The internal 1A..5
    workflow codes are translated here and never leave the server: **no code, no
    fee and no action** appears anywhere in this response, which is R39's own
    acceptance line and is asserted structurally rather than by careful wording.

    Basis: **NET EUR** — every amount is a `vat_eur` figure and crosses the wire
    as an exact decimal STRING (§4.9). A claim's `vat_eur` is `null` when no
    single-currency figure can be stated for it yet (a draft whose lines span
    currencies, §4.14, or a filed claim whose frozen column is NULL); it is
    never `0.00` as a stand-in, and such a claim contributes nothing to any
    stage total.

    A claim in an engine state outside the ladder is counted in
    `not_shown_claims` without naming that state, so `Σ stages.claims +
    not_shown_claims == total_claims` holds exactly while the internal
    vocabulary stays internal.

    A year with no claims is a 200 with six empty stages and zeroes — never a
    404, never an exception. Only a year outside the four-digit range a
    `ref_period` can carry is refused (422 `invalid_year`).
    """
    view = await client_status_svc.client_claim_status(db, current.org_id, year)
    return ClientClaimStatusOut(
        year=view.year,
        currency=view.currency,
        total_claims=view.total_claims,
        stages=[
            ClientStageOut(
                stage=s.stage,
                label=s.label,
                description=s.description,
                claims=s.claims,
                vat_eur=s.vat_eur,
            )
            for s in view.stages
        ],
        claims=[
            ClientClaimOut(
                entity_name=c.entity_name,
                refund_country=c.refund_country,
                period=c.period,
                stage=c.stage,
                vat_eur=c.vat_eur,
            )
            for c in view.claims
        ],
        not_shown_claims=view.not_shown_claims,
    )
