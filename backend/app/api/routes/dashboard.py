"""The composed home dashboard route (WO-16 / I1.1) — thin controller over
`services/dashboard.home`, an Insight-layer projection (ADR-0023)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentOrg, CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.dashboard import DashboardOut, OnboardingOut
from app.services import audit, dashboard, onboarding

# Gate (ADR-0024): INVOICE_READ is the ONE permission every business role holds
# (verified against ROLE_PERMISSIONS — EMPLOYEE and READ_ONLY included), so every
# member gets a home page. The real narrowing happens per-section inside the
# service, using the SAME permission each canonical surface's own route declares
# — the composed endpoint never widens what any role could already read.
router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_READ))],
)


@router.get("", response_model=DashboardOut)
async def get_dashboard(current: CurrentUser, db: DbSession):
    """Everything that needs the caller today, composed from the canonical read
    services; sections the caller may not see are null, never zeroed."""
    return await dashboard.home(db, current, current.org_id)


@router.get("/onboarding", response_model=OnboardingOut)
async def get_onboarding(current: CurrentUser, org: CurrentOrg, db: DbSession):
    """The derived setup checklist (WO-P / R19). Same INVOICE_READ gate as the
    dashboard it sits on; every step is recomputed from existing rows, so this
    read can never disagree with the screens that actually complete the steps."""
    data = await onboarding.checklist(db, org)
    return OnboardingOut(**data, can_dismiss=authz.has(current, authz.Permission.SETTINGS_MANAGE))


@router.post(
    "/onboarding/dismiss",
    response_model=OnboardingOut,
    dependencies=[Depends(require_perm(authz.Permission.SETTINGS_MANAGE))],
)
async def dismiss_onboarding(current: CurrentUser, org: CurrentOrg, db: DbSession):
    """Close the card for the WHOLE workspace — an org-level presentation
    choice, so it takes the same authority as the settings screen it lives
    beside. Idempotent; audited with what was still undone at the time."""
    data = await onboarding.checklist(db, org)
    onboarding.dismiss(org)
    await audit.record(
        db,
        "onboarding.dismissed",
        target_type="organization",
        target_id=org.id,
        meta={"undone": [s["key"] for s in data["steps"] if not s["done"]]},
    )
    await db.commit()
    data = await onboarding.checklist(db, org)
    return OnboardingOut(**data, can_dismiss=True)
