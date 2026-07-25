"""Dunning-ladder configuration (Phase 16).

Read and replace the per-tenant overdue-reminder escalation ladder used by
services/dunning.run_overdue. Admin-only (SETTINGS_MANAGE). With no configured
levels the org runs the built-in default ladder — GET reports that via `is_default`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.models.dunning_policy import DunningPolicy
from app.schemas.dunning import DunningLevelOut, DunningPolicyIn, DunningPolicyOut
from app.services import audit, dunning

# Structural authorization (ADR-0024): the dunning ladder is org configuration —
# router-level SETTINGS_MANAGE (both read and write, as before).
router = APIRouter(
    prefix="/dunning",
    tags=["dunning"],
    dependencies=[Depends(require_perm(authz.Permission.SETTINGS_MANAGE))],
)


@router.get("/policy", response_model=DunningPolicyOut)
async def get_policy(current: CurrentUser, db: DbSession):
    configured = list(
        await db.scalars(
            select(DunningPolicy)
            .where(DunningPolicy.org_id == current.org_id)
            .order_by(DunningPolicy.level)
        )
    )
    if configured:
        return DunningPolicyOut(
            is_default=False,
            levels=[
                DunningLevelOut(
                    level=p.level, days_overdue=p.days_overdue, tone=p.tone, active=p.active
                )
                for p in configured
            ],
        )
    return DunningPolicyOut(
        is_default=True,
        levels=[
            DunningLevelOut(level=lv.level, days_overdue=lv.days_overdue, tone=lv.tone, active=True)
            for lv in dunning.DEFAULT_LADDER
        ],
    )


@router.put("/policy", response_model=DunningPolicyOut)
async def set_policy(body: DunningPolicyIn, current: CurrentUser, db: DbSession):
    levels = sorted(body.levels, key=lambda lv: lv.level)
    if len({lv.level for lv in levels}) != len(levels):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "duplicate level")
    # Replace the whole ladder for this org.
    await db.execute(delete(DunningPolicy).where(DunningPolicy.org_id == current.org_id))
    for lv in levels:
        db.add(
            DunningPolicy(
                org_id=current.org_id,
                level=lv.level,
                days_overdue=lv.days_overdue,
                tone=lv.tone,
                active=lv.active,
            )
        )
    await audit.record(
        db,
        audit.A.DUNNING_CONFIG,
        target_type="dunning_policy",
        target_id=current.org_id,
        meta={"levels": len(levels)},
    )
    await db.commit()
    return await get_policy(current, db)
