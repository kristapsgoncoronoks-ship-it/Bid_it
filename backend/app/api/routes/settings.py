from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentOrg, CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.validation import ValidationSettings, ValidationSettingsUpdate

# Structural authorization (ADR-0024): validation settings are org
# configuration — router-level SETTINGS_MANAGE (previously the read was open to
# any member; WO-1 newly gates it).
router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(require_perm(authz.Permission.SETTINGS_MANAGE))],
)


@router.get("/validation", response_model=ValidationSettings)
async def get_validation_settings(current: CurrentUser, db: DbSession, org: CurrentOrg):
    return ValidationSettings(
        ai_validation_enabled=org.ai_validation_enabled,
        human_validation_enabled=org.human_validation_enabled,
    )


@router.put("/validation", response_model=ValidationSettings)
async def update_validation_settings(
    body: ValidationSettingsUpdate, current: CurrentUser, db: DbSession, org: CurrentOrg
):
    if body.ai_validation_enabled is not None:
        org.ai_validation_enabled = body.ai_validation_enabled
    if body.human_validation_enabled is not None:
        org.human_validation_enabled = body.human_validation_enabled
    await db.commit()
    return ValidationSettings(
        ai_validation_enabled=org.ai_validation_enabled,
        human_validation_enabled=org.human_validation_enabled,
    )
