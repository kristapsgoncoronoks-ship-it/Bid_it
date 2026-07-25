from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentOrg, CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.module import ModuleOut, ModuleToggle
from app.services import audit, issuer, modules, plans

# Structural authorization (ADR-0024): module entitlements are org
# configuration — router-level SETTINGS_MANAGE (previously the list was open to
# any member; WO-1 newly gates it).
router = APIRouter(
    prefix="/modules",
    tags=["modules"],
    dependencies=[Depends(require_perm(authz.Permission.SETTINGS_MANAGE))],
)


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
        out.append(
            ModuleOut(
                key=m.key,
                name=m.name,
                description=m.description,
                core=m.core,
                enabled=m.key in enabled,
                requires_issuer=m.requires_issuer,
                ready=await _ready(db, current.org_id, m),
            )
        )
    return out


@router.put("/{key}", response_model=ModuleOut)
async def toggle_module(
    key: str, body: ModuleToggle, current: CurrentUser, db: DbSession, org: CurrentOrg
):
    m = modules.MODULES_BY_KEY.get(key)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown module")
    if m.core:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Core modules are always on")

    # Add-on modules are gated by the subscription plan.
    if body.enabled:
        if not plans.allows_module(org.plan, key):
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                f"The {m.name} module isn't included in your {plans.plan_for(org.plan).name} plan. Upgrade to enable it.",
            )
    await modules.set_enabled(db, current.org_id, key, body.enabled)
    await audit.record(
        db,
        audit.A.MODULE_TOGGLE,
        target_type="module",
        target_id=key,
        meta={"enabled": body.enabled},
    )
    await db.commit()
    return ModuleOut(
        key=m.key,
        name=m.name,
        description=m.description,
        core=m.core,
        enabled=body.enabled,
        requires_issuer=m.requires_issuer,
        ready=await _ready(db, current.org_id, m),
    )
