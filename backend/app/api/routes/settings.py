from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.models.organization import Organization
from app.schemas.validation import ValidationSettings, ValidationSettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/validation", response_model=ValidationSettings)
async def get_validation_settings(current: CurrentUser, db: DbSession):
    org = await db.get(Organization, current.org_id)
    return ValidationSettings(
        ai_validation_enabled=org.ai_validation_enabled,
        human_validation_enabled=org.human_validation_enabled,
    )


@router.put("/validation", response_model=ValidationSettings)
async def update_validation_settings(
    body: ValidationSettingsUpdate, current: CurrentUser, db: DbSession
):
    authz.require(current, authz.Permission.SETTINGS_MANAGE)
    org = await db.get(Organization, current.org_id)
    if body.ai_validation_enabled is not None:
        org.ai_validation_enabled = body.ai_validation_enabled
    if body.human_validation_enabled is not None:
        org.human_validation_enabled = body.human_validation_enabled
    await db.commit()
    return ValidationSettings(
        ai_validation_enabled=org.ai_validation_enabled,
        human_validation_enabled=org.human_validation_enabled,
    )
