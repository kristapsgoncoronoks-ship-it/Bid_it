"""GDPR right-to-erasure (DSAR) endpoints (Phase 4, ADR-0020). Admin-gated,
tenant-scoped. Preview classifies every personal-data location; execute performs
the erasable ones and reports what was deliberately retained (statutory /
integrity) or blocked by a legal hold."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.core.roles import is_admin_or_above
from app.schemas.privacy import ErasureLocationOut, ErasureReportOut, ErasureRequest
from app.services import privacy

router = APIRouter(prefix="/privacy", tags=["privacy"])


def _require_admin(current) -> None:
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can process erasure requests")


def _out(rep) -> ErasureReportOut:
    return ErasureReportOut(
        email=rep.email,
        on_hold=rep.on_hold,
        executed=rep.executed,
        locations=[
            ErasureLocationOut(
                key=loc.key,
                label=loc.label,
                matched=loc.matched,
                action=loc.action,
                reason=loc.reason,
            )
            for loc in rep.locations
        ],
    )


@router.post("/erasure/preview", response_model=ErasureReportOut)
async def preview_erasure(body: ErasureRequest, current: CurrentUser, db: DbSession):
    _require_admin(current)
    return _out(await privacy.preview(db, current.org_id, str(body.email)))


@router.post("/erasure", response_model=ErasureReportOut)
async def run_erasure(body: ErasureRequest, current: CurrentUser, db: DbSession):
    _require_admin(current)
    return _out(await privacy.erase(db, current.org_id, str(body.email), actor_email=current.email))
