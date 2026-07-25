from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.budget import BudgetOverview, BudgetTargetIn, BudgetTargetOut
from app.services import budget, modules

# Structural authorization (ADR-0024): budget vs actual reads the org's spend
# aggregates — router-level REPORT_READ; setting/removing targets is org
# configuration — SETTINGS_MANAGE per-route. (Previously open to any member.)
router = APIRouter(
    prefix="/budget",
    tags=["budget"],
    dependencies=[Depends(require_perm(authz.Permission.REPORT_READ))],
)
_ADMIN = [Depends(require_perm(authz.Permission.SETTINGS_MANAGE))]


async def _guard(db: DbSession, org_id: str):
    await modules.require_enabled(db, org_id, "budget")


@router.get("/overview", response_model=BudgetOverview)
async def overview(
    current: CurrentUser,
    db: DbSession,
    month: str = Query(description="Budget month as YYYY-MM"),
):
    await _guard(db, current.org_id)
    try:
        year, mon = budget.parse_month(month)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    return await budget.overview(db, current.org_id, year, mon)


@router.get("/targets", response_model=list[BudgetTargetOut])
async def list_targets(current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    return [
        BudgetTargetOut.model_validate(t) for t in await budget.list_targets(db, current.org_id)
    ]


@router.put("/targets", response_model=BudgetTargetOut, dependencies=_ADMIN)
async def set_target(body: BudgetTargetIn, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    row = await budget.set_target(db, current.org_id, body.category, body.monthly_limit)
    return BudgetTargetOut.model_validate(row)


@router.delete("/targets/{category}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_ADMIN)
async def delete_target(category: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    if not await budget.delete_target(db, current.org_id, category):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No budget target for that category")
