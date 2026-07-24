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
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
