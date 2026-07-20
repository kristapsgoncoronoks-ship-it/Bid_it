"""Immutable, hash-chained audit trail — the spine of internal-audit controls.

`record()` appends one tamper-evident event per action (who, what, target, when),
chaining each event's hash into the next. `verify_chain()` walks a tenant's events
and reports the first break (a deleted or edited row). Reads are tenant-scoped.

Recording is best-effort: an audit failure must never break the user's operation,
but it is logged loudly. The event is added to the caller's session and commits
atomically with the operation it describes.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import get_current_actor, get_current_org
from app.models.audit import AuditEvent

log = logging.getLogger("invoiceiq.audit")


# Canonical action names (dot-namespaced: domain.verb).
class A:
    LOGIN = "auth.login"
    REGISTER = "auth.register"
    INVOICE_CREATE = "invoice.create"
    INVOICE_DELETE = "invoice.delete"
    INVOICE_VALIDATE = "invoice.validate"
    ISSUED_PAYMENT = "issued.payment"
    ISSUED_SENT = "issued.sent"
    ISSUED_REMINDER = "issued.reminder"
    ISSUED_PENALTY = "issued.penalty_invoice"
    PARTNER_CREATE = "partner.create"
    PARTNER_DOC_SIGN = "partner.document_sign"
    DOC_DOWNLOAD = "document.download"
    INBOUND_CONFIRM = "inbound.confirm"
    MODULE_TOGGLE = "module.toggle"
    ROLE_CHANGE = "user.role_change"
    APPROVER_CHANGE = "user.approver_change"
    USER_DEACTIVATE = "user.deactivate"
    INVITE_CREATE = "user.invite"


def _hash(prev_hash: str | None, seq: int, org_id: str, actor_id: str | None,
          action: str, target_type: str | None, target_id: str | None,
          at_ms: int, meta_json: str | None) -> str:
    payload = "|".join([
        prev_hash or "", str(seq), org_id, actor_id or "", action,
        target_type or "", target_id or "", str(at_ms), meta_json or "",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def record(
    db: AsyncSession,
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    meta: dict | None = None,
    org_id: str | None = None,
    actor: tuple[str | None, str | None] | None = None,
) -> AuditEvent | None:
    """Append one audit event (chained to the tenant's previous one). Best-effort:
    never raises — a failure is logged and returns None. The event is added to the
    session; the caller's commit persists it atomically with its operation."""
    try:
        org = org_id or get_current_org()
        if org is None:
            return None  # no tenant context (bootstrap) — nothing to attribute
        actor_id, actor_email = actor if actor is not None else get_current_actor()

        latest = await db.scalar(
            select(AuditEvent).where(AuditEvent.org_id == org).order_by(AuditEvent.seq.desc()).limit(1)
        )
        seq = (latest.seq + 1) if latest else 1
        prev_hash = latest.hash if latest else None
        at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        meta_json = json.dumps(meta, sort_keys=True, separators=(",", ":")) if meta else None
        h = _hash(prev_hash, seq, org, actor_id, action, target_type, target_id, at_ms, meta_json)

        event = AuditEvent(
            org_id=org, seq=seq, actor_id=actor_id, actor_email=actor_email,
            action=action, target_type=target_type, target_id=target_id,
            meta=meta_json, at_ms=at_ms, prev_hash=prev_hash, hash=h,
        )
        db.add(event)
        # Flush so a SECOND record() in the same transaction (e.g. a bulk action)
        # sees this event when it computes the next seq — otherwise both would
        # claim the same seq and collide on (org_id, seq).
        await db.flush()
        return event
    except Exception as exc:  # never break the operation being audited
        log.warning("audit.record failed for %s: %s", action, exc)
        return None


@dataclass
class ChainStatus:
    ok: bool
    events: int
    broken_at_seq: int | None = None
    detail: str | None = None


async def verify_chain(db: AsyncSession, org_id: str) -> ChainStatus:
    """Recompute the hash chain for a tenant and report the first break."""
    rows = list(await db.scalars(
        select(AuditEvent).where(AuditEvent.org_id == org_id).order_by(AuditEvent.seq.asc())
    ))
    prev = None
    for e in rows:
        expected_prev = prev.hash if prev else None
        if e.prev_hash != expected_prev:
            return ChainStatus(False, len(rows), e.seq, "prev_hash does not link to the previous event")
        recomputed = _hash(e.prev_hash, e.seq, e.org_id, e.actor_id, e.action,
                           e.target_type, e.target_id, e.at_ms, e.meta)
        if recomputed != e.hash:
            return ChainStatus(False, len(rows), e.seq, "event hash does not match its contents")
        prev = e
    return ChainStatus(True, len(rows))


async def list_events(
    db: AsyncSession, org_id: str, *, action: str | None = None,
    page: int = 1, page_size: int = 50,
) -> tuple[list[AuditEvent], int]:
    filters = [AuditEvent.org_id == org_id]
    if action:
        filters.append(AuditEvent.action == action)
    total = await db.scalar(select(func.count(AuditEvent.id)).where(*filters)) or 0
    rows = list(await db.scalars(
        select(AuditEvent).where(*filters).order_by(AuditEvent.seq.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ))
    return rows, total
