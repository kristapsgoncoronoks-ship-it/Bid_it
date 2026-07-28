from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentOrg, CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.access import PlanPolicyOut, PlanPolicyUpdate, UsageOut
from app.services import access
from app.services import plans as plans_service

# Structural authorization (ADR-0024): the limits matrix is administration data
# — SETTINGS_MANAGE per-route (WO-1 newly gates the read; editing additionally
# requires the platform-operator flag in-handler, which is STRICTER than any
# tenant permission). `GET /usage` stays self-service — every tier reads its own
# org's quota (see PUBLIC_ROUTES + test_access) — so it deliberately declares
# nothing.
router = APIRouter(prefix="/access", tags=["access"])
_ADMIN = [Depends(require_perm(authz.Permission.SETTINGS_MANAGE))]


@router.get("/matrix", response_model=list[PlanPolicyOut], dependencies=_ADMIN)
async def get_matrix(current: CurrentUser, db: DbSession):
    """The system matrix — per-PLAN usage limits (WO-47: keyed by the org's
    subscription plan, not the caller's role), shared across ALL companies.
    Reading it is administration (SETTINGS_MANAGE — WO-1 gated what used to be
    open to any member; an org's OWN usage comes from `GET /usage`); only a
    PLATFORM OPERATOR may edit it. A company owner administers their own company
    but has no system-wide power, so cannot change limits that affect every
    company."""
    return await access.matrix(db)


@router.put("/matrix/{plan}", response_model=PlanPolicyOut, dependencies=_ADMIN)
async def set_matrix_row(plan: str, body: PlanPolicyUpdate, current: CurrentUser, db: DbSession):
    # The matrix is global (not tenant-scoped), so only a platform operator may
    # edit it — never a company owner (that would be a cross-company privilege).
    if not current.is_platform_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only a platform operator can change the global limits matrix",
        )
    if plan not in plans_service.PLANS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown plan")
    return await access.set_limits(db, plan, body.monthly_invoice_limit, body.monthly_upload_limit)


@router.get("/usage", response_model=UsageOut)
async def my_usage(current: CurrentUser, current_org: CurrentOrg, db: DbSession):
    """The caller's ORGANIZATION's current usage vs its plan's limit (this
    month) — WO-47: every member of the org sees the SAME org-wide figures,
    regardless of their own role."""
    return await access.usage(db, current.org_id, current_org.plan)
