from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.models.user import UserRole
from app.schemas.access import RolePolicyOut, RolePolicyUpdate, UsageOut
from app.services import access

router = APIRouter(prefix="/access", tags=["access"])


@router.get("/matrix", response_model=list[RolePolicyOut])
async def get_matrix(current: CurrentUser, db: DbSession):
    """The system matrix — per-role usage limits, shared across ALL companies.
    Any signed-in user can read it (so the UI can show their own limits); only a
    PLATFORM OPERATOR may edit it. A company owner administers their own company
    but has no system-wide power, so cannot change limits that affect every
    company."""
    return await access.matrix(db)


@router.put("/matrix/{role}", response_model=RolePolicyOut)
async def set_matrix_row(role: str, body: RolePolicyUpdate, current: CurrentUser, db: DbSession):
    # The matrix is global (not tenant-scoped), so only a platform operator may
    # edit it — never a company owner (that would be a cross-company privilege).
    if not current.is_platform_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only a platform operator can change the global limits matrix",
        )
    try:
        role_enum = UserRole(role)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown role")
    return await access.set_limits(
        db, role_enum, body.monthly_invoice_limit, body.monthly_upload_limit
    )


@router.get("/usage", response_model=UsageOut)
async def my_usage(current: CurrentUser, db: DbSession):
    """The caller's current usage vs their access-level limit (this month)."""
    return await access.usage(db, current.org_id, current.role)
