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
    # Capture review (E1.1): a human recording per-field corrections on an
    # extraction run — advisory provenance, not a financial mutation, but §4.16
    # still applies (every mutating operation is audited).
    CAPTURE_REVIEW = "capture.field_review"
    POLICY_CREATE = "approval_policy.create"
    POLICY_UPDATE = "approval_policy.update"
    POLICY_DELETE = "approval_policy.delete"
    ISSUED_PAYMENT = "issued.payment"
    DUNNING_CONFIG = "dunning.config"
    AP_PAYMENT = "ap.payment"
    AP_RUN_CREATE = "ap.run_create"
    AP_RUN_APPROVE = "ap.run_approve"
    AP_RUN_PAID = "ap.run_paid"
    AP_RUN_CANCEL = "ap.run_cancel"
    # WO-9 payment-run controls (names fixed by the work order — keep verbatim).
    AP_RUN_SOD_OVERRIDE = "payment_run.sod_override"
    AP_RUN_EXPORTED = "payment_run.exported"
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
    PARTNER_UPDATE = "partner.update"
    PARTNER_DOC_UPLOAD = "partner.document_upload"
    PARTNER_DOC_SIGN = "partner.document_sign"
    PARTNER_DOC_DELETE = "partner.document_delete"
    DOC_DOWNLOAD = "document.download"
    INBOUND_CONFIRM = "inbound.confirm"
    MODULE_TOGGLE = "module.toggle"
    # Expense reimbursement (Phase 09)
    REIMBURSE_BATCH = "expense.reimbursement_batch"
    REIMBURSE_PAID = "expense.reimbursement_paid"
    REIMBURSE_CANCEL = "expense.reimbursement_cancel"
    REIMBURSE_EXPORTED = "expense.reimbursement_exported"
    # R6 (§4.8 SoD): the audited, explicit platform-admin override of the
    # reimbursement-payout maker≠checker control (mirrors AP_RUN_SOD_OVERRIDE).
    REIMBURSE_SOD_OVERRIDE = "reimbursement.sod_override"
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
    # Tenant & session integrity (WO-4)
    SESSION_REVOKE_BULK = "session.revoked_bulk"
    TENANT_UPDATE = "platform.tenant_update"
    # Dogfood subscription billing (H1.6 / WO-48): InvoiceIQ invoicing its own
    # tenant customers through its own AR module.
    PLATFORM_SUBSCRIPTION_INVOICE = "platform.subscription_invoice"
    # Master-data catalogs (WO-14 / F1.1): tax codes, currencies, costing
    # masters. Not financial mutations themselves, but they configure how money
    # is rated and allocated — §4.16 applies to them like any other mutation.
    TAX_CODE_CREATE = "tax_code.create"
    TAX_CODE_SET_ACTIVE = "tax_code.set_active"
    CURRENCY_CREATE = "currency.create"
    CURRENCY_SET_ACTIVE = "currency.set_active"
    MASTER_CREATE = "master.create"
    MASTER_UPDATE = "master.update"
    # Transport vertical — EU cross-border VAT refund claims (M3 / WO-49, ADR-P3).
    # Only the grain-construction action exists yet; submission/lock/fee actions
    # land with the future work orders that build them.
    TRANSPORT_CLAIM_CREATE = "transport.claim_create"
    # G1.2 (WO-50): idempotent fuel-transaction ingestion. Fires once per
    # actual insert; a natural-key replay is a no-op and audits nothing.
    FUEL_TRANSACTION_INGEST = "transport.fuel_transaction_ingest"
    # G2.2 (WO-51): the one-invoice-one-submission lock (R4/R5). SUBMIT fires
    # once per successful draft->submitted transition (a lost lock race rolls
    # back the whole transaction and audits nothing); WITHDRAW fires once per
    # explicit release — the ONLY function that ever removes a lock row.
    TRANSPORT_CLAIM_SUBMIT = "transport.claim_submit"
    TRANSPORT_CLAIM_WITHDRAW = "transport.claim_withdraw"
    # G2.4 (WO-52): note→invoice resolution + live claim-line construction.
    # NOTE_OVERRIDE_SET fires on both create and update of an admin-curated
    # override (C4/R16 — an override changes only the invoice ASSOCIATION,
    # never an amount). CLAIM_LINES_BUILD fires once per (re)build call — a
    # draft claim's lines are rebuildable, so this is NOT an insert-or-no-op
    # action the way FUEL_TRANSACTION_INGEST is.
    TRANSPORT_NOTE_OVERRIDE_SET = "transport.note_override_set"
    TRANSPORT_CLAIM_LINES_BUILD = "transport.claim_lines_build"
    # G1.3 (WO-53): the monthly close as a durable job. Fires once per close
    # RUN (one audit trail per R31), whether it processed zero or many
    # claims — never once per claim (that would be `claim_lines_build`'s job,
    # already fired by `build_claim_lines` itself for each claim it touches).
    TRANSPORT_CLOSE_RUN = "transport.close_run"
    # G2.6 slice 3 (WO-58): receipt-control waivers (R15). SET fires once per
    # NEW waiver row (idempotent — a repeat call on the same (claim,
    # supplier) key is a no-op and audits nothing); REMOVE fires once per
    # actual un-waive (a no-op remove, nothing left to delete, audits
    # nothing either).
    TRANSPORT_RECEIPT_WAIVER_SET = "transport.receipt_waiver_set"
    TRANSPORT_RECEIPT_WAIVER_REMOVE = "transport.receipt_waiver_remove"
    # G2.7 (WO-59): the workflow status_code LABEL (2A..5), never the coarse
    # engine `status` column — see status.py's module docstring for why the
    # engine-state transitions themselves are deferred (entangled with the
    # decision-gated G2.9 fee engine).
    TRANSPORT_STATUS_CODE_SET = "transport.status_code_set"
    # G2.10 slice 1 (WO-60): the adjustable submission-checklist rule table.
    # Fires on `set_active` (old->new); a first-seed insert of a new rule
    # also fires (a fact appearing), a repeat `seed_default_rules` call
    # audits nothing (a true no-op).
    TRANSPORT_CHECKLIST_RULE_SET_ACTIVE = "transport.checklist_rule_set_active"
    # G3.1 slice 1 (WO-61): per-country supplier legal-entity registrations.
    # Fires on every admin-curated `set_registration` write AND on a
    # `learn_registration` call that actually inserts a NEW row; a
    # no-op learn call (a row already exists) audits nothing.
    TRANSPORT_SUPPLIER_REGISTRATION_SET = "transport.supplier_registration_set"
    # G3.3 (WO-66): the engine tie-out expectations a human types from the
    # invoice PDF. SET fires on create AND on a retype (old->new both
    # recorded); REMOVE fires on delete (the narrow un-halt for a
    # mis-typed supplier).
    TRANSPORT_TIEOUT_EXPECTATION_SET = "transport.tieout_expectation_set"
    TRANSPORT_TIEOUT_EXPECTATION_REMOVE = "transport.tieout_expectation_remove"
    # G3.3 slice 2 (WO-70): the anti-drift extraction baseline. SET fires
    # on the confirm-time first record (old=None) AND on an explicit,
    # human-initiated rebaseline (old->new both recorded); REMOVE fires on
    # delete (the narrow fail-open mitigation for a noisy digest). The
    # drift CHECK itself is a read and deliberately audits nothing.
    TRANSPORT_EXTRACTION_BASELINE_SET = "transport.extraction_baseline_set"
    TRANSPORT_EXTRACTION_BASELINE_REMOVE = "transport.extraction_baseline_remove"
    # G3.5 (WO-72): receipt control (cadence × activity). CADENCE_SET fires
    # on create AND on a changed value (old->new both recorded; an
    # idempotent repeat audits nothing); CADENCE_REMOVE on delete.
    # CONTROL_RUN is one whole-org batch summary per run (the CLOSE_RUN
    # pattern). CONTROL_OVERRIDE fires when set_control_override — the ONLY
    # writer of the waived/note override columns — actually changes one.
    # The control's findings (missing slots, orphans) are advisory reads
    # and deliberately audit nothing of their own (§4.19).
    TRANSPORT_SUPPLIER_CADENCE_SET = "transport.supplier_cadence_set"
    TRANSPORT_SUPPLIER_CADENCE_REMOVE = "transport.supplier_cadence_remove"
    TRANSPORT_RECEIPT_CONTROL_RUN = "transport.receipt_control_run"
    TRANSPORT_RECEIPT_CONTROL_OVERRIDE = "transport.receipt_control_override"
    # G2.11 (WO-73, R44): customer lifecycle + per-country activation.
    # LIFECYCLE_SET fires on every REAL customer-state transition
    # (add_prospect's create, promote/activate/deactivate/set_inactive),
    # old->new in meta; the idempotent add_prospect repeat audits nothing.
    # COUNTRY_ACTIVATION_SET is the same contract for the per-country rows.
    # enforce_activation (the R44 gate) is a read and audits nothing —
    # the refusal/success is already visible on the claim's own
    # TRANSPORT_CLAIM_SUBMIT trail.
    TRANSPORT_CUSTOMER_LIFECYCLE_SET = "transport.customer_lifecycle_set"
    TRANSPORT_COUNTRY_ACTIVATION_SET = "transport.country_activation_set"
    # G4.5 (WO-82, R41): supplier contract terms + the overcharge claim-back.
    # CONTRACT_TERM_SET/REMOVE audit the agreed €/L figures old->new, because
    # those figures determine a euro this platform then demands from a supplier.
    # OVERCHARGE_OPEN records the FROZEN detected exposure the demand quotes;
    # OVERCHARGE_TRANSITION records every real lifecycle move old->new
    # (including the booked cash on `recovered`). Detection itself
    # (`contract_audit.audit`) is a read and audits nothing — R41's own
    # "read-only over the analytics" (§4.19).
    TRANSPORT_CONTRACT_TERM_SET = "transport.contract_term_set"
    TRANSPORT_CONTRACT_TERM_REMOVE = "transport.contract_term_remove"
    TRANSPORT_OVERCHARGE_OPEN = "transport.overcharge_open"
    TRANSPORT_OVERCHARGE_TRANSITION = "transport.overcharge_transition"


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
