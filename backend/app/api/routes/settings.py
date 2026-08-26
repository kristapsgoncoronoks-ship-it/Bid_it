from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

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
        overcharge_block_enabled=org.overcharge_block_enabled,
    )


@router.put("/validation", response_model=ValidationSettings)
async def update_validation_settings(
    body: ValidationSettingsUpdate, current: CurrentUser, db: DbSession, org: CurrentOrg
):
    if body.ai_validation_enabled is not None:
        org.ai_validation_enabled = body.ai_validation_enabled
    if body.human_validation_enabled is not None:
        org.human_validation_enabled = body.human_validation_enabled
    if body.overcharge_block_enabled is not None:
        org.overcharge_block_enabled = body.overcharge_block_enabled
    await db.commit()
    return ValidationSettings(
        ai_validation_enabled=org.ai_validation_enabled,
        human_validation_enabled=org.human_validation_enabled,
        overcharge_block_enabled=org.overcharge_block_enabled,
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


# WO-E: schedule-notice org settings — ONE surface for both audiences.
# assignment_remind_hours: lead for the EMPLOYEE reminder (NULL = the 24h code
# default). client_notice_hours: lead for the CUSTOMER arrival notice — NULL
# means OFF (outward email is opt-in), and the UI offers 24/48/72; the server
# pins that set so a typo can't schedule a notice a week out.
_CLIENT_NOTICE_CHOICES = (24, 48, 72)


class ScheduleSettings(BaseModel):
    assignment_remind_hours: int | None
    client_notice_hours: int | None


class ScheduleSettingsUpdate(BaseModel):
    assignment_remind_hours: int | None = Field(default=None, ge=1, le=336)
    clear_assignment_remind_hours: bool = False
    client_notice_hours: int | None = None
    clear_client_notice_hours: bool = False


@router.get("/schedule", response_model=ScheduleSettings)
async def get_schedule_settings(current: CurrentUser, db: DbSession, org: CurrentOrg):
    return ScheduleSettings(
        assignment_remind_hours=org.assignment_remind_hours,
        client_notice_hours=org.client_notice_hours,
    )


@router.put("/schedule", response_model=ScheduleSettings)
async def update_schedule_settings(
    body: ScheduleSettingsUpdate, current: CurrentUser, db: DbSession, org: CurrentOrg
):
    if body.clear_assignment_remind_hours:
        org.assignment_remind_hours = None
    elif body.assignment_remind_hours is not None:
        org.assignment_remind_hours = body.assignment_remind_hours
    if body.clear_client_notice_hours:
        org.client_notice_hours = None
    elif body.client_notice_hours is not None:
        if body.client_notice_hours not in _CLIENT_NOTICE_CHOICES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "client_notice_hours must be one of 24, 48 or 72",
            )
        org.client_notice_hours = body.client_notice_hours
    await audit.record(
        db,
        "settings.schedule_update",
        target_type="organization",
        target_id=org.id,
        meta={
            "assignment_remind_hours": org.assignment_remind_hours,
            "client_notice_hours": org.client_notice_hours,
        },
    )
    await db.commit()
    await db.refresh(org)
    return ScheduleSettings(
        assignment_remind_hours=org.assignment_remind_hours,
        client_notice_hours=org.client_notice_hours,
    )


# --------------------------------------------------------------------------- #
# WO-K: the statutory late-interest reference rate (2011/7/EU). The figure the
# advisory computation adds 8 pp to. NULL = the service's stated default; an
# admin types the current ECB main refinancing rate here — ADR-0027 forbids
# fetching it ambiently, and this number moves only with policy decisions.
# --------------------------------------------------------------------------- #

from decimal import Decimal  # noqa: E402

from app.services.late_interest import (  # noqa: E402
    DEFAULT_BASE_RATE_PP,
    STATUTORY_MARGIN_PP,
)


class LateInterestSettings(BaseModel):
    base_rate_pp: Decimal | None
    default_base_rate_pp: Decimal
    statutory_margin_pp: Decimal


class LateInterestSettingsUpdate(BaseModel):
    base_rate_pp: Decimal | None = Field(default=None, ge=0, le=25)
    clear_base_rate: bool = False


@router.get("/late-interest", response_model=LateInterestSettings)
async def get_late_interest_settings(current: CurrentUser, db: DbSession, org: CurrentOrg):
    return LateInterestSettings(
        base_rate_pp=org.late_interest_base_rate,
        default_base_rate_pp=DEFAULT_BASE_RATE_PP,
        statutory_margin_pp=STATUTORY_MARGIN_PP,
    )


@router.put("/late-interest", response_model=LateInterestSettings)
async def update_late_interest_settings(
    body: LateInterestSettingsUpdate, current: CurrentUser, db: DbSession, org: CurrentOrg
):
    if body.clear_base_rate:
        org.late_interest_base_rate = None
    elif body.base_rate_pp is not None:
        org.late_interest_base_rate = body.base_rate_pp
    await audit.record(
        db,
        "settings.late_interest_update",
        target_type="organization",
        target_id=org.id,
        meta={"base_rate_pp": str(org.late_interest_base_rate)},
    )
    await db.commit()
    await db.refresh(org)
    return LateInterestSettings(
        base_rate_pp=org.late_interest_base_rate,
        default_base_rate_pp=DEFAULT_BASE_RATE_PP,
        statutory_margin_pp=STATUTORY_MARGIN_PP,
    )
