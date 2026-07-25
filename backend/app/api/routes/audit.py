from __future__ import annotations

import json
from datetime import UTC, date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core.authz import Permission
from app.schemas.audit import AuditEventOut, AuditListOut, ChainStatusOut
from app.services import audit, audit_export

# Reading the audit trail requires AUDIT_READ (owner, administrator, finance
# manager, auditor) — declared structurally on the router (ADR-0024), enforced
# through the authz matrix, never a raw role check.
router = APIRouter(
    prefix="/audit",
    tags=["audit"],
    dependencies=[Depends(require_perm(Permission.AUDIT_READ))],
)


def _day_bounds_ms(from_: str | None, to: str | None) -> tuple[int | None, int | None]:
    """Parse inclusive YYYY-MM-DD bounds → (since_ms, until_ms) in UTC epoch ms."""

    def _ms(d: date, end: bool) -> int:
        t = time.max if end else time.min
        return int(datetime.combine(d, t, tzinfo=UTC).timestamp() * 1000)

    try:
        since = _ms(date.fromisoformat(from_), False) if from_ else None
        until = _ms(date.fromisoformat(to), True) if to else None
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "from/to must be ISO dates (YYYY-MM-DD)")
    return since, until


def _out(e) -> AuditEventOut:
    meta = None
    if e.meta:
        try:
            meta = json.loads(e.meta)
        except ValueError:
            meta = None
    return AuditEventOut(
        id=e.id,
        seq=e.seq,
        actor_email=e.actor_email,
        action=e.action,
        target_type=e.target_type,
        target_id=e.target_id,
        meta=meta,
        at=datetime.fromtimestamp(e.at_ms / 1000, tz=UTC),
    )


@router.get("", response_model=AuditListOut)
async def list_audit(
    current: CurrentUser,
    db: DbSession,
    action: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    rows, total = await audit.list_events(
        db, current.org_id, action=action, page=page, page_size=page_size
    )
    return AuditListOut(items=[_out(e) for e in rows], total=total)


@router.get("/verify", response_model=ChainStatusOut)
async def verify(current: CurrentUser, db: DbSession):
    """Recompute the hash chain and report the first break (tamper check)."""
    s = await audit.verify_chain(db, current.org_id)
    return ChainStatusOut(ok=s.ok, events=s.events, broken_at_seq=s.broken_at_seq, detail=s.detail)


@router.get("/export")
async def export_audit(
    current: CurrentUser,
    db: DbSession,
    fmt: str = Query("csv"),
    action: str | None = Query(default=None),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
):
    """Download the tenant's audit trail (CSV or JSON) for auditors. Includes the
    seq/prev_hash/hash columns so the chain can be re-verified independently."""
    if fmt not in audit_export.FORMATS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown format '{fmt}'. Available: {', '.join(audit_export.FORMATS)}.",
        )
    since_ms, until_ms = _day_bounds_ms(from_, to)
    events = await audit.export_events(
        db, current.org_id, action=action, since_ms=since_ms, until_ms=until_ms
    )
    chain = await audit.verify_chain(db, current.org_id)
    filename, text, media_type = audit_export.render(fmt, events, chain, org_id=current.org_id)
    return Response(
        content=text,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Audit-Chain-Verified": "true" if chain.ok else "false",
            "X-Audit-Event-Count": str(len(events)),
        },
    )
