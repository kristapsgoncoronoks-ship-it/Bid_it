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


# --------------------------------------------------------------------------- #
# WO-D: project-lifecycle org settings — the client-set offer prefix (the
# numbering scheme is THEIRS; the platform enforces only uniqueness) and the
# final-invoice acceptance gate (linked by default, gated on opt-in).
# --------------------------------------------------------------------------- #

from pydantic import BaseModel, Field  # noqa: E402

from app.services import audit  # noqa: E402


class LifecycleSettings(BaseModel):
    offer_prefix: str | None
    final_invoice_requires_acceptance: bool


class LifecycleSettingsUpdate(BaseModel):
    offer_prefix: str | None = Field(default=None, max_length=20)
    clear_offer_prefix: bool = False
    final_invoice_requires_acceptance: bool | None = None


@router.get("/lifecycle", response_model=LifecycleSettings)
async def get_lifecycle_settings(current: CurrentUser, db: DbSession, org: CurrentOrg):
    return LifecycleSettings(
        offer_prefix=org.offer_prefix,
        final_invoice_requires_acceptance=org.final_invoice_requires_acceptance,
    )


@router.put("/lifecycle", response_model=LifecycleSettings)
async def update_lifecycle_settings(
    body: LifecycleSettingsUpdate, current: CurrentUser, db: DbSession, org: CurrentOrg
):
    if body.clear_offer_prefix:
        org.offer_prefix = None
    elif body.offer_prefix is not None:
        org.offer_prefix = body.offer_prefix.strip() or None
    if body.final_invoice_requires_acceptance is not None:
        org.final_invoice_requires_acceptance = body.final_invoice_requires_acceptance
    await audit.record(
        db,
        "settings.lifecycle_update",
        target_type="organization",
        target_id=org.id,
        meta={
            "offer_prefix": org.offer_prefix,
            "final_invoice_requires_acceptance": org.final_invoice_requires_acceptance,
        },
    )
    await db.commit()
    await db.refresh(org)
    return LifecycleSettings(
        offer_prefix=org.offer_prefix,
        final_invoice_requires_acceptance=org.final_invoice_requires_acceptance,
    )
