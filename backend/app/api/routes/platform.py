"""Platform operator (cross-tenant) endpoints — for the SaaS provider.

Strictly gated to `is_platform_admin`. Returns tenant METADATA only (never any
tenant's invoice data) and can suspend/reactivate or re-plan a tenant.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, get_current_user
from app.models.organization import Organization
from app.models.user import User
from app.schemas.tenancy import TenantOut, TenantUpdate
from app.services import plans

router = APIRouter(prefix="/platform", tags=["platform"])


async def require_platform_admin(current: User = Depends(get_current_user)) -> User:
    if not current.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform operator access required")
    # Operator reads across tenants → drop the single-tenant scope for this request.
    from app.core.tenant import set_current_org

    set_current_org(None)
    return current


PlatformAdmin = CurrentUser  # alias documented; real gate below via dependency


@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(db: DbSession, _: User = Depends(require_platform_admin)):
    orgs = await db.scalars(select(Organization).order_by(Organization.created_at.desc()))
    out = []
    for org in orgs:
        out.append(
            TenantOut(
                id=org.id,
                name=org.name,
                plan=org.plan,
                status=org.status,
                seats_used=await plans.active_seats(db, org.id),
                created_at=org.created_at,
            )
        )
    return out


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: str, body: TenantUpdate, db: DbSession, _: User = Depends(require_platform_admin)
):
    org = await db.get(Organization, tenant_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    if body.status is not None:
        if body.status not in ("active", "suspended", "canceled"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid status")
        org.status = body.status
    if body.plan is not None:
        if body.plan not in plans.PLANS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown plan")
        org.plan = body.plan
    await db.commit()
    return TenantOut(
        id=org.id,
        name=org.name,
        plan=org.plan,
        status=org.status,
        seats_used=await plans.active_seats(db, org.id),
        created_at=org.created_at,
    )
