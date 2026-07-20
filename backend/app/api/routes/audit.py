from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession
from app.core.roles import is_owner
from app.schemas.audit import AuditEventOut, AuditListOut, ChainStatusOut
from app.services import audit

router = APIRouter(prefix="/audit", tags=["audit"])


def _require_owner(current):
    if not is_owner(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the company owner can view the audit trail")


def _out(e) -> AuditEventOut:
    meta = None
    if e.meta:
        try:
            meta = json.loads(e.meta)
        except ValueError:
            meta = None
    return AuditEventOut(
        id=e.id, seq=e.seq, actor_email=e.actor_email, action=e.action,
        target_type=e.target_type, target_id=e.target_id, meta=meta,
        at=datetime.fromtimestamp(e.at_ms / 1000, tz=timezone.utc),
    )


@router.get("", response_model=AuditListOut)
async def list_audit(
    current: CurrentUser,
    db: DbSession,
    action: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    _require_owner(current)
    rows, total = await audit.list_events(db, current.org_id, action=action, page=page, page_size=page_size)
    return AuditListOut(items=[_out(e) for e in rows], total=total)


@router.get("/verify", response_model=ChainStatusOut)
async def verify(current: CurrentUser, db: DbSession):
    """Recompute the hash chain and report the first break (tamper check)."""
    _require_owner(current)
    s = await audit.verify_chain(db, current.org_id)
    return ChainStatusOut(ok=s.ok, events=s.events, broken_at_seq=s.broken_at_seq, detail=s.detail)
