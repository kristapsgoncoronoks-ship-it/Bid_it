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
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import get_current_actor, get_current_org
from app.models.audit import AuditEvent

log = logging.getLogger("invoiceiq.audit")


# Canonical action names (dot-namespaced: domain.verb).
class A:
    LOGIN = "auth.login"
    REGISTER = "auth.register"
    EMAIL_VERIFY = "auth.email_verify"
    PASSWORD_RESET_REQUEST = "auth.password_reset_request"
    PASSWORD_RESET = "auth.password_reset"
    SWITCH_ORG = "auth.switch_org"
    INVOICE_CREATE = "invoice.create"
    INVOICE_DELETE = "invoice.delete"
    INVOICE_VALIDATE = "invoice.validate"
    # AP review & approval lifecycle (Phase 08)
    INVOICE_EDIT = "invoice.edit"
    INVOICE_TRANSITION = "invoice.transition"
    INVOICE_SUBMIT = "invoice.submit"
    INVOICE_APPROVE = "invoice.approve"
    INVOICE_REJECT = "invoice.reject"
    INVOICE_RETURN = "invoice.return_for_correction"
    INVOICE_REASSIGN = "invoice.reassign_approver"
    INVOICE_CORRECTION = "invoice.controlled_correction"
    INVOICE_LOCK = "invoice.lock"
    INVOICE_COMMENT = "invoice.comment"
    INVOICE_ATTACH = "invoice.attachment"
    POLICY_CREATE = "approval_policy.create"
    POLICY_UPDATE = "approval_policy.update"
    POLICY_DELETE = "approval_policy.delete"
    ISSUED_PAYMENT = "issued.payment"
    DUNNING_CONFIG = "dunning.config"
    AP_PAYMENT = "ap.payment"
    AP_RUN_CREATE = "ap.run_create"
    AP_RUN_PAID = "ap.run_paid"
    AP_RUN_CANCEL = "ap.run_cancel"
    ISSUED_SENT = "issued.sent"
    ISSUED_REMINDER = "issued.reminder"
    ISSUED_PENALTY = "issued.penalty_invoice"
    ISSUED_CREDIT_NOTE = "issued.credit_note"
    ISSUED_VOID = "issued.void"
    # Explicit invoice lifecycle (INV-3)
    ISSUED_CREATE = "issued.create"
    ISSUED_EDIT = "issued.edit"
    ISSUED_APPROVE = "issued.approve"
    ISSUED_ISSUE = "issued.issue"
    ISSUED_DUPLICATE = "issued.duplicate"
    ISSUED_CANCEL = "issued.cancel"
    ISSUED_DISPUTE = "issued.dispute"
    ISSUED_UNDISPUTE = "issued.undispute"
    ISSUED_WRITE_OFF = "issued.write_off"
    ISSUED_ATTACH = "issued.attachment"
    ISSUED_VIEWED = "issued.viewed"
    RECURRING_CREATE = "recurring.create"
    RECURRING_UPDATE = "recurring.update"
    RECURRING_GENERATE = "recurring.generate"
    PARTNER_CREATE = "partner.create"
    PARTNER_DOC_SIGN = "partner.document_sign"
    DOC_DOWNLOAD = "document.download"
    INBOUND_CONFIRM = "inbound.confirm"
    MODULE_TOGGLE = "module.toggle"
    # Expense reimbursement (Phase 09)
    REIMBURSE_BATCH = "expense.reimbursement_batch"
    REIMBURSE_PAID = "expense.reimbursement_paid"
    REIMBURSE_CANCEL = "expense.reimbursement_cancel"
    EXPENSE_POLICY_SET = "expense.policy_set"
    EXPENSE_TRANSITION = "expense.transition"
    EXPENSE_SUBMIT = "expense.submit"
    EXPENSE_APPROVE = "expense.approve"
    EXPENSE_REJECT = "expense.reject"
    EXPENSE_RETURN = "expense.return_for_correction"
    EXPENSE_REASSIGN = "expense.reassign_approver"
    EXPENSE_APPROVAL_POLICY = "expense.approval_policy"
    # Vendor master data + the protected-field change workflow (WO-2)
    VENDOR_CREATE = "vendor.create"
    VENDOR_UPDATE = "vendor.update"
    VENDOR_CHANGE_REQUEST = "vendor.change_requested"
    VENDOR_CHANGE_APPROVE = "vendor.change_approved"
    VENDOR_CHANGE_REJECT = "vendor.change_rejected"
    ROLE_CHANGE = "user.role_change"
    APPROVER_CHANGE = "user.approver_change"
    USER_DEACTIVATE = "user.deactivate"
    INVITE_CREATE = "user.invite"


def _hash(
    prev_hash: str | None,
    seq: int,
    org_id: str,
    actor_id: str | None,
    action: str,
    target_type: str | None,
    target_id: str | None,
    at_ms: int,
    meta_json: str | None,
) -> str:
    payload = "|".join(
        [
            prev_hash or "",
            str(seq),
            org_id,
            actor_id or "",
            action,
            target_type or "",
            target_id or "",
            str(at_ms),
            meta_json or "",
        ]
    )
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

        # Serialize per-tenant audit appends so the hash-chain seq is collision-free
        # under concurrency: without this, two concurrent same-org writes compute the
        # same seq and the second hits the (org_id, seq) unique constraint at the
        # caller's commit — OUTSIDE this try/except — which would 500 the whole
        # operation, violating the best-effort contract. A Postgres transaction-scoped
        # advisory lock (released at commit/rollback) makes the read-then-insert atomic;
        # SQLite already serializes writers, so it is a no-op there.
        bind = db.bind
        if bind is not None and bind.dialect.name == "postgresql":
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"audit:{org}"}
            )

        latest = await db.scalar(
            select(AuditEvent)
            .where(AuditEvent.org_id == org)
            .order_by(AuditEvent.seq.desc())
            .limit(1)
        )
        seq = (latest.seq + 1) if latest else 1
        prev_hash = latest.hash if latest else None
        at_ms = int(datetime.now(UTC).timestamp() * 1000)
        meta_json = json.dumps(meta, sort_keys=True, separators=(",", ":")) if meta else None
        h = _hash(prev_hash, seq, org, actor_id, action, target_type, target_id, at_ms, meta_json)

        event = AuditEvent(
            org_id=org,
            seq=seq,
            actor_id=actor_id,
            actor_email=actor_email,
            action=action,
            target_type=target_type,
            target_id=target_id,
            meta=meta_json,
            at_ms=at_ms,
            prev_hash=prev_hash,
            hash=h,
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
    rows = list(
        await db.scalars(
            select(AuditEvent).where(AuditEvent.org_id == org_id).order_by(AuditEvent.seq.asc())
        )
    )
    prev = None
    for e in rows:
        expected_prev = prev.hash if prev else None
        if e.prev_hash != expected_prev:
            return ChainStatus(
                False, len(rows), e.seq, "prev_hash does not link to the previous event"
            )
        recomputed = _hash(
            e.prev_hash,
            e.seq,
            e.org_id,
            e.actor_id,
            e.action,
            e.target_type,
            e.target_id,
            e.at_ms,
            e.meta,
        )
        if recomputed != e.hash:
            return ChainStatus(False, len(rows), e.seq, "event hash does not match its contents")
        prev = e
    return ChainStatus(True, len(rows))


async def list_events(
    db: AsyncSession,
    org_id: str,
    *,
    action: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[AuditEvent], int]:
    filters = [AuditEvent.org_id == org_id]
    if action:
        filters.append(AuditEvent.action == action)
    total = await db.scalar(select(func.count(AuditEvent.id)).where(*filters)) or 0
    rows = list(
        await db.scalars(
            select(AuditEvent)
            .where(*filters)
            .order_by(AuditEvent.seq.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


async def export_events(
    db: AsyncSession,
    org_id: str,
    *,
    action: str | None = None,
    since_ms: int | None = None,
    until_ms: int | None = None,
) -> list[AuditEvent]:
    """Every matching event in CHRONOLOGICAL order (ascending seq) — the natural
    order for an evidence export and for independently re-verifying the hash
    chain. Optional action + inclusive time-window (epoch ms) filters."""
    filters = [AuditEvent.org_id == org_id]
    if action:
        filters.append(AuditEvent.action == action)
    if since_ms is not None:
        filters.append(AuditEvent.at_ms >= since_ms)
    if until_ms is not None:
        filters.append(AuditEvent.at_ms <= until_ms)
    return list(await db.scalars(select(AuditEvent).where(*filters).order_by(AuditEvent.seq.asc())))
