"""Data retention + legal hold (Phase 4, ADR-0019). Admin-gated, tenant-scoped.

Retention windows are opt-in per category (absence = keep forever). A legal hold
suspends all purging while active. `POST /retention/purge` runs a purge on demand
(the scheduler runs it daily); both honour the hold and are audited.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.core.roles import is_admin_or_above
from app.schemas.retention import (
    HoldCreate,
    LegalHoldOut,
    PolicyUpdate,
    PurgeResult,
    RetentionCategoryOut,
    RetentionOut,
)
from app.services import retention as svc

router = APIRouter(prefix="/retention", tags=["retention"])


def _require_admin(current) -> None:
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can manage data retention")


async def _snapshot(db, org_id: str) -> RetentionOut:
    policies = await svc.get_policies(db, org_id)
    prev = await svc.preview(db, org_id)
    cats = [
        RetentionCategoryOut(
            key=c.key,
            label=c.label,
            retain_days=policies.get(c.key),
            purgeable_now=prev.counts.get(c.key, 0),
        )
        for c in svc.CATEGORIES.values()
    ]
    holds = [LegalHoldOut.model_validate(h) for h in await svc.active_holds(db, org_id)]
    return RetentionOut(categories=cats, on_hold=prev.on_hold, holds=holds)


@router.get("", response_model=RetentionOut)
async def get_retention(current: CurrentUser, db: DbSession):
    _require_admin(current)
    return await _snapshot(db, current.org_id)


@router.put("/policy", response_model=RetentionOut)
async def set_policy(body: PolicyUpdate, current: CurrentUser, db: DbSession):
    _require_admin(current)
    try:
        await svc.set_policy(db, current.org_id, body.category, body.retain_days)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return await _snapshot(db, current.org_id)


@router.post("/holds", response_model=LegalHoldOut, status_code=status.HTTP_201_CREATED)
async def place_hold(body: HoldCreate, current: CurrentUser, db: DbSession):
    _require_admin(current)
    hold = await svc.place_hold(db, current.org_id, reason=body.reason, actor_email=current.email)
    return LegalHoldOut.model_validate(hold)


@router.post("/holds/{hold_id}/release", response_model=RetentionOut)
async def release_hold(hold_id: str, current: CurrentUser, db: DbSession):
    _require_admin(current)
    ok = await svc.release_hold(db, current.org_id, hold_id, actor_email=current.email)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active hold with that id")
    return await _snapshot(db, current.org_id)


@router.post("/purge", response_model=PurgeResult)
async def run_purge(current: CurrentUser, db: DbSession):
    """Run a retention purge now (in addition to the daily scheduled run)."""
    _require_admin(current)
    result = await svc.purge(db, current.org_id)
    return PurgeResult(**result)
