from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.core.roles import is_sysadmin
from app.models.user import UserRole
from app.schemas.access import RolePolicyOut, RolePolicyUpdate, UsageOut
from app.services import access

router = APIRouter(prefix="/access", tags=["access"])


@router.get("/matrix", response_model=list[RolePolicyOut])
async def get_matrix(current: CurrentUser, db: DbSession):
    """The system matrix — per-role usage limits. Any signed-in user can read it
    (so the UI can show their own limits); only a sysadmin can edit it."""
    return await access.matrix(db)


@router.put("/matrix/{role}", response_model=RolePolicyOut)
async def set_matrix_row(role: str, body: RolePolicyUpdate, current: CurrentUser, db: DbSession):
    if not is_sysadmin(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a sysadmin can change the access matrix")
    try:
        role_enum = UserRole(role)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown role")
    return await access.set_limits(db, role_enum, body.monthly_invoice_limit, body.monthly_upload_limit)


@router.get("/usage", response_model=UsageOut)
async def my_usage(current: CurrentUser, db: DbSession):
    """The caller's current usage vs their access-level limit (this month)."""
    return await access.usage(db, current.org_id, current.role)
