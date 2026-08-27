"""Transport routes slice 8 — the supplier-reliability board (WO-Q).

Thin controllers over `app.services.transport.reliability`: structural
permission gate → call the already-gated service → shape the response
(engineering-rules §3). Every refusal a client can see (`module_not_enabled`,
`invalid_threshold`) is raised BY THE SERVICE as an `app.core.errors.AppError`
and rendered by the one `app.main` handler, so this module maps nothing and the
wire vocabulary cannot drift from the service layer.

AUTHORIZATION (ADR-0024, structural)
--------------------------------------
Router-level **`TRANSPORT_READ`** — WO-79's reserved permission for exactly
this kind of derived-analytics surface, already claimed by WO-81/87/91. The
ONE write here (the org's own band thresholds) overrides to the EXISTING
`VAT_WRITE`, the same authority that sets excise and fee rates. **No permission
member is added** (§10).

WHY THE ONLY WRITE IS A THRESHOLD
-----------------------------------
The rating itself is derived and stores nothing, so there is nothing here to
open, freeze, package or send — and no expression through which a reliability
figure could reach the euro a payment demand quotes. What an operator CAN
change is their own tolerance, which is configuration, audited old→new like
every other transport registry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.transport_reliability import (
    CriterionOut,
    ReliabilityOut,
    SupplierReliabilityOut,
    ThresholdsIn,
    ThresholdsOut,
)
from app.services.transport import reliability

router = APIRouter(
    prefix="/transport/reliability",
    tags=["transport"],
    dependencies=[Depends(require_perm(authz.Permission.TRANSPORT_READ))],
)


def _thresholds_out(t: reliability.Thresholds) -> ThresholdsOut:
    return ThresholdsOut(
        overcharge_cases=t.overcharge_cases,
        overcharge_eur_per_1000=t.overcharge_eur_per_1000,
        fx_markup_bps=t.fx_markup_bps,
        ungoverned_share_pct=t.ungoverned_share_pct,
        is_default=t.is_default,
    )


@router.get("", response_model=ReliabilityOut)
async def get_reliability(current: CurrentUser, db: DbSession):
    """The board: one entry per supplier active in the rolling window, each
    criterion carrying its band, the rule that produced it, and the figures
    behind it. Read-only."""
    rep = await reliability.report(db, current.org_id)
    return ReliabilityOut(
        window_from=rep.window_from,
        window_to=rep.window_to,
        framing=rep.framing,
        thresholds=_thresholds_out(rep.thresholds),
        suppliers=[
            SupplierReliabilityOut(
                supplier=s.supplier,
                overall=s.overall,
                active_months=s.active_months,
                net_spend_eur=s.net_spend_eur,
                criteria=[
                    CriterionOut(key=c.key, band=c.band, rule=c.rule, figures=c.figures)
                    for c in s.criteria
                ],
            )
            for s in rep.suppliers
        ],
    )


@router.get("/thresholds", response_model=ThresholdsOut)
async def get_thresholds(current: CurrentUser, db: DbSession):
    return _thresholds_out(await reliability.get_thresholds(db, current.org_id))


@router.put(
    "/thresholds",
    response_model=ThresholdsOut,
    dependencies=[Depends(require_perm(authz.Permission.VAT_WRITE))],
)
async def put_thresholds(body: ThresholdsIn, current: CurrentUser, db: DbSession):
    """Set the org's own band boundaries. Audited old→new, defaults included —
    moving off the platform default is itself a decision worth recording."""
    out = await reliability.set_thresholds(
        db,
        current.org_id,
        overcharge_cases=body.overcharge_cases,
        overcharge_eur_per_1000=body.overcharge_eur_per_1000,
        fx_markup_bps=body.fx_markup_bps,
        ungoverned_share_pct=body.ungoverned_share_pct,
    )
    await db.commit()
    return _thresholds_out(out)
