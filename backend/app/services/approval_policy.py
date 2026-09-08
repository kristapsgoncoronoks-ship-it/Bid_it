"""Approval-policy evaluation + chain lifecycle (Phase 08, deliverable 3).

Two responsibilities:

1. **Evaluation** — given an invoice, find the approval policy that governs it.
   `evaluate()` walks the org's ACTIVE policies in `priority` order and returns
   the first whose criteria ALL match (an unset criterion is a wildcard). The
   initial practical subset of criteria: amount threshold, department, cost
   center, legal entity, supplier.

2. **Chain lifecycle** — `build_chain()` expands the matching policy into ordered
   `ApprovalStep` rows on submit (one per sequential approver, plus an optional
   finance-final step; a no-policy submit gets one generic step approvable by any
   approver). `chain_state()` derives the invoice's workflow state from its steps,
   and `pending_step()`/`is_assigned_approver()` drive the approve/reject route.

Pure DB + logic — no HTTP. Concurrency on a policy edit raises
`PolicyConcurrencyError` (the route maps it to 409); the invoice's own optimistic
lock is handled separately in `invoice_workflow.assert_version`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Boolean, and_, bindparam, exists, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.approval import (
    KIND_APPROVER,
    KIND_FINANCE_FINAL,
    STEP_APPROVED,
    STEP_PENDING,
    STEP_REJECTED,
    STEP_SKIPPED,
    ApprovalPolicy,
    ApprovalStep,
)
from app.models.invoice import Invoice, WorkflowState
from app.models.vendor import Vendor


class PolicyError(Exception):
    """Base for policy-service errors."""


class PolicyConcurrencyError(PolicyError):
    """A policy was edited concurrently (stale expected_version) → 409."""


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def _invoice_amount_eur(invoice: Invoice) -> Decimal:
    """The amount an amount-threshold criterion compares against (EUR)."""
    val = invoice.total_eur if invoice.total_eur is not None else invoice.total
    return Decimal(val or 0)


def matches(policy: ApprovalPolicy, invoice: Invoice) -> bool:
    """Whether every SET criterion on the policy matches the invoice."""
    if policy.min_amount is not None and _invoice_amount_eur(invoice) < Decimal(policy.min_amount):
        return False
    if policy.department_id and invoice.department_id != policy.department_id:
        return False
    if policy.cost_center_id and invoice.cost_center_id != policy.cost_center_id:
        return False
    if policy.legal_entity_id and invoice.legal_entity_id != policy.legal_entity_id:
        return False
    if policy.vendor_id and invoice.vendor_id != policy.vendor_id:
        return False
    return True


async def evaluate(db: AsyncSession, org_id: str, invoice: Invoice) -> ApprovalPolicy | None:
    """The governing policy for an invoice, or None (→ a single generic step)."""
    policies = list(
        await db.scalars(
            select(ApprovalPolicy)
            .where(ApprovalPolicy.org_id == org_id, ApprovalPolicy.active.is_(True))
            .order_by(ApprovalPolicy.priority.asc(), ApprovalPolicy.created_at.asc())
        )
    )
    for p in policies:
        if matches(p, invoice):
            return p
    return None


def _approver_ids(policy: ApprovalPolicy | None) -> list[str]:
    if policy is None or not policy.approver_ids:
        return []
    try:
        raw = json.loads(policy.approver_ids)
        return [str(x) for x in raw if x]
    except (ValueError, TypeError):
        return []


# --------------------------------------------------------------------------- #
# Chain lifecycle
# --------------------------------------------------------------------------- #


async def build_chain(
    db: AsyncSession, org_id: str, invoice: Invoice, policy: ApprovalPolicy | None
) -> list[ApprovalStep]:
    """Create the ordered approval steps for a submission. A policy with named
    approvers yields one step each (+ optional finance-final); no policy (or an
    empty chain) yields ONE generic step approvable by any INVOICE_APPROVE holder.
    Flushes; the caller commits."""
    steps: list[ApprovalStep] = []
    seq = 0
    for aid in _approver_ids(policy):
        steps.append(
            ApprovalStep(
                org_id=org_id,
                invoice_id=invoice.id,
                policy_id=policy.id if policy else None,
                seq=seq,
                kind=KIND_APPROVER,
                approver_id=aid,
                status=STEP_PENDING,
            )
        )
        seq += 1
    if policy is not None and policy.finance_final:
        steps.append(
            ApprovalStep(
                org_id=org_id,
                invoice_id=invoice.id,
                policy_id=policy.id,
                seq=seq,
                kind=KIND_FINANCE_FINAL,
                approver_id=policy.finance_approver_id,
                status=STEP_PENDING,
            )
        )
        seq += 1
    if not steps:
        steps.append(
            ApprovalStep(
                org_id=org_id,
                invoice_id=invoice.id,
                policy_id=policy.id if policy else None,
                seq=0,
                kind=KIND_APPROVER,
                approver_id=None,
                status=STEP_PENDING,
            )
        )
    db.add_all(steps)
    await db.flush()
    return steps


async def steps_for(db: AsyncSession, org_id: str, invoice_id: str) -> list[ApprovalStep]:
    return list(
        await db.scalars(
            select(ApprovalStep)
            .where(ApprovalStep.org_id == org_id, ApprovalStep.invoice_id == invoice_id)
            .order_by(ApprovalStep.seq.asc())
        )
    )


def pending_step(steps: list[ApprovalStep]) -> ApprovalStep | None:
    """The next step awaiting a decision (lowest seq still pending)."""
    for s in sorted(steps, key=lambda s: s.seq):
        if s.status == STEP_PENDING:
            return s
    return None


def chain_state(steps: list[ApprovalStep]) -> WorkflowState:
    """Derive the invoice workflow state from its approval steps."""
    if any(s.status == STEP_REJECTED for s in steps):
        return WorkflowState.rejected
    pending = [s for s in steps if s.status == STEP_PENDING]
    approved = [s for s in steps if s.status == STEP_APPROVED]
    if not pending:
        return WorkflowState.approved
    if approved:
        return WorkflowState.partially_approved
    return WorkflowState.submitted


def is_assigned_approver(step: ApprovalStep, user) -> bool:
    """Whether `user` is allowed to decide THIS step. A step with no named
    approver is open to any INVOICE_APPROVE holder (the route enforces the
    permission); a named step is restricted to that approver (or a platform admin)."""
    if step.approver_id is None:
        return True
    if getattr(user, "is_platform_admin", False):
        return True
    return step.approver_id == user.id


def skip_pending(steps: list[ApprovalStep]) -> None:
    """Mark every still-pending step skipped (used on reject / return-for-correction)."""
    for s in steps:
        if s.status == STEP_PENDING:
            s.status = STEP_SKIPPED


# --------------------------------------------------------------------------- #
# Cross-invoice inbox (WO-16 / I1.1)
# --------------------------------------------------------------------------- #

# Workflow states in which an approval chain is live (a pending step is actionable).
_IN_APPROVAL_STATES = (WorkflowState.submitted, WorkflowState.partially_approved)


@dataclass
class ApInboxItem:
    """One supplier invoice whose CURRENT approval step awaits the given user."""

    invoice_id: str
    invoice_number: str
    vendor_name: str | None
    total: Decimal
    currency: str


def _build_inbox_statement():
    """The AP-inbox SELECT with bound parameters (see ``waiting_for``)."""
    current_step = aliased(ApprovalStep, name="current_step")
    earlier_pending = aliased(ApprovalStep, name="earlier_pending")
    org_id = bindparam("org_id")
    user_id = bindparam("user_id")
    can_approve_any = bindparam("can_approve_any", type_=Boolean)

    actionable = or_(
        current_step.approver_id == user_id,
        and_(can_approve_any == true(), current_step.approver_id.is_(None)),
    )

    # The org predicate inside this subquery is LOAD-BEARING: the ORM tenant
    # guard's loader criteria reach entities in the columns clause, the joins
    # and their aliases, but not a table that enters only through a WHERE
    # clause — this EXISTS (R4/R5 panels, captured guarded SQL). Only this
    # line and RLS scope it.
    no_earlier_pending = ~exists(
        select(1).where(
            earlier_pending.org_id == org_id,
            earlier_pending.invoice_id == current_step.invoice_id,
            earlier_pending.status == STEP_PENDING,
            earlier_pending.seq < current_step.seq,
        )
    )

    return (
        select(Invoice.id, Invoice.invoice_number, Vendor.name, Invoice.total, Invoice.currency)
        .select_from(current_step)
        .join(
            Invoice,
            and_(Invoice.id == current_step.invoice_id, Invoice.org_id == current_step.org_id),
        )
        .outerjoin(Vendor, and_(Vendor.id == Invoice.vendor_id, Vendor.org_id == org_id))
        .where(
            current_step.org_id == org_id,
            current_step.status == STEP_PENDING,
            Invoice.workflow_state.in_(_IN_APPROVAL_STATES),
            or_(Invoice.submitted_by.is_(None), Invoice.submitted_by != user_id),
            actionable,
            no_earlier_pending,
        )
        .order_by(Invoice.submitted_at.desc().nulls_last(), current_step.seq.asc())
        .limit(bindparam("limit"))
    )


_INBOX_STMT = _build_inbox_statement()


async def waiting_for(
    db: AsyncSession,
    org_id: str,
    *,
    user_id: str,
    can_approve_any: bool,
    limit: int = 10,
) -> list[ApInboxItem]:
    """The canonical "AP approvals waiting on me" inbox (the composed home
    dashboard projects this — ADR-0023: one query, never re-derived elsewhere).

    An invoice counts only when its CURRENT step (lowest ``seq`` still pending —
    a later pending step of the chain is not yet actionable) is either assigned
    to ``user_id`` or open (``approver_id IS NULL``) while the caller holds
    INVOICE_APPROVE (``can_approve_any``). Invoices the user submitted are
    EXCLUDED — segregation of duties (§4.8) forbids acting on them (parity with
    the route's ``_guard_decider`` for every caller except a platform admin,
    whom the route lets bypass SoD and assignment; their inbox undercounts what
    the route would let them decide), so counting them would invite a forbidden
    action. Newest submissions first; read-only, mutates nothing.

    Execution shape (PERF-DUCK-001 / PERF-018, reference integration R4 — the
    DuckDB filter/projection-pushdown lesson): ONE projected SELECT. The
    "current step" rule is a correlated ``NOT EXISTS`` (no earlier pending step
    of the same invoice), the SoD and assignee predicates are WHERE clauses,
    only the five columns the inbox shows are projected, and ``LIMIT`` runs in
    SQL. The previous shape hydrated every pending step + full invoice, loaded
    vendors in a second SELECT and deduplicated/filtered/limited in Python —
    measurable CPU on the contended dashboard loop. Semantics unchanged; the
    org predicates stay explicit on every table (RLS is the backstop, not the
    filter).

    The statement is built ONCE at import (``_INBOX_STMT``) and executed with
    bound parameters: measured on the perf workspace, building it per call —
    two ``aliased()`` column collections, the subquery, the joins — cost ~3 ms
    of pure Python per dashboard request, more than the old shape's empty
    hydration (PERF-DUCK-002 re-measurement, `docs/perf/TENANT-GUARD-2026-09-08.md`).
    """
    rows = await db.execute(
        _INBOX_STMT,
        {
            "org_id": org_id,
            "user_id": user_id,
            "can_approve_any": bool(can_approve_any),
            "limit": int(limit),
        },
    )
    return [
        ApInboxItem(
            invoice_id=invoice_id,
            invoice_number=invoice_number,
            vendor_name=vendor_name,
            total=Decimal(total),
            currency=currency,
        )
        for invoice_id, invoice_number, vendor_name, total, currency in rows
    ]
