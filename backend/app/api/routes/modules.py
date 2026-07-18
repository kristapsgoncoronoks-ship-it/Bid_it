from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.models.organization import Organization
from app.models.user import UserRole
from app.schemas.module import ModuleOut, ModuleToggle
from app.services import issuer, modules, plans

router = APIRouter(prefix="/modules", tags=["modules"])


async def _ready(db: DbSession, org_id: str, m: modules.Module) -> bool:
    if not m.requires_issuer:
        return True
    profile = await issuer.get_or_create(db, org_id)
    return issuer.is_complete(profile)


@router.get("", response_model=list[ModuleOut])
async def list_modules(current: CurrentUser, db: DbSession):
    enabled = await modules.enabled_keys(db, current.org_id)
    out = []
    for m in modules.MODULES:
        out.append(ModuleOut(
            key=m.key, name=m.name, description=m.description, core=m.core,
            enabled=m.key in enabled, requires_issuer=m.requires_issuer,
            ready=await _ready(db, current.org_id, m),
        ))
    return out


@router.put("/{key}", response_model=ModuleOut)
async def toggle_module(key: str, body: ModuleToggle, current: CurrentUser, db: DbSession):
    if current.role != UserRole.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the workspace owner can change modules")
    m = modules.MODULES_BY_KEY.get(key)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown module")
    if m.core:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Core modules are always on")

    # Add-on modules are gated by the subscription plan.
    if body.enabled:
        org = await db.get(Organization, current.org_id)
        if not plans.allows_module(org.plan, key):
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                f"The {m.name} module isn't included in your {plans.plan_for(org.plan).name} plan. Upgrade to enable it.",
            )
    await modules.set_enabled(db, current.org_id, key, body.enabled)
    return ModuleOut(
        key=m.key, name=m.name, description=m.description, core=m.core,
        enabled=body.enabled, requires_issuer=m.requires_issuer,
        ready=await _ready(db, current.org_id, m),
    )
