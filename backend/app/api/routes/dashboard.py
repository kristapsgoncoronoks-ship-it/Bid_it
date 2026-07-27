"""The composed home dashboard route (WO-16 / I1.1) — thin controller over
`services/dashboard.home`, an Insight-layer projection (ADR-0023)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.dashboard import DashboardOut
from app.services import dashboard

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
