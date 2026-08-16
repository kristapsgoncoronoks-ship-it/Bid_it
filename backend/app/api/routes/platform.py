"""Platform operator (cross-tenant) endpoints — for the SaaS provider.

Strictly gated to `is_platform_admin`. Returns tenant METADATA only (never any
tenant's invoice data) and can suspend/reactivate or re-plan a tenant.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, get_current_user_unscoped
from app.core import authz
from app.models.organization import Organization
from app.models.user import User
from app.schemas.tenancy import TenantOut, TenantUpdate
from app.services import archive, audit, plans, sessions

router = APIRouter(prefix="/platform", tags=["platform"])


async def require_platform_admin(current: User = Depends(get_current_user_unscoped)) -> User:
    """Platform-operator gate, built on the UNSCOPED dependency deliberately
    (WO-4): an operator acts ACROSS tenants — including SUSPENDED ones, which is
    the only way a suspension gets reversed — so the tenant path's per-request
    org-status and membership gates must not apply here. Token signature, live
    session (jti) and `is_active` are still enforced by `_authenticate`, and the
    tenant ContextVar stays None, so nothing here is tenant-scoped."""
    if not current.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform operator access required")
    return current


# Structural authorization (ADR-0024): the platform gate is STRICTER than any
# tenant permission (an org OWNER holds every Permission yet must never pass),
# so it declares the PLATFORM_ADMIN sentinel rather than a Permission — the
# coverage test recognises it through the same introspection attribute.
setattr(require_platform_admin, authz.PERMISSIONS_ATTR, (authz.PLATFORM_ADMIN,))

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
                archive_retention_years=org.archive_retention_years,
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
    changes: dict = {}
    if body.status is not None:
        if body.status not in ("active", "suspended", "canceled"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid status")
        if body.status != org.status:
            changes["status"] = {"old": org.status, "new": body.status}
        org.status = body.status
        if body.status != "active" and changes.get("status"):
            # WO-4: pulling the suspension lever kills every live session whose
            # ACTIVE org is this tenant, immediately — the per-request org-status
            # gate in deps is the enforcement; this makes the cut permanent, so
            # reactivating the org does NOT resurrect old tokens (fresh login
            # required). One-way by design.
            n = await sessions.revoke_bulk(db, org_id=org.id)
            await audit.record(
                db,
                audit.A.SESSION_REVOKE_BULK,
                org_id=org.id,
                target_type="organization",
                target_id=org.id,
                meta={"trigger": f"org_{body.status}", "count": n},
            )
    if body.plan is not None:
        if body.plan not in plans.PLANS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown plan")
        if body.plan != org.plan:
            changes["plan"] = {"old": org.plan, "new": body.plan}
        org.plan = body.plan
    if body.archive_retention_years is not None:
        # 0 clears the grant back to the included tier. The service only ever
        # EXTENDS existing rows (never shortens), and clearing re-stamps
        # nothing — records already kept under a longer promise keep it.
        years = body.archive_retention_years or None
        if years != org.archive_retention_years:
            grant = await archive.apply_retention_override(db, org.id, years)
            changes["archive_retention_years"] = {
                "old": grant["old"],
                "new": years,
                "rows_extended": grant["rows_extended"],
            }
    if changes:
        # Audit the operator action itself (old→new), attributed to the tenant's
        # own trail so the tenant's audit export shows who re-planned/suspended it.
        await audit.record(
            db,
            audit.A.TENANT_UPDATE,
            org_id=org.id,
            target_type="organization",
            target_id=org.id,
            meta=changes,
        )
    await db.commit()
    return TenantOut(
        id=org.id,
        name=org.name,
        plan=org.plan,
        status=org.status,
        seats_used=await plans.active_seats(db, org.id),
        created_at=org.created_at,
        archive_retention_years=org.archive_retention_years,
    )
