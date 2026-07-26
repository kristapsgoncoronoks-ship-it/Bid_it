"""Multi-step expense-approval routing service (mirrors approval_policy.py).

Pure DB + logic, no HTTP. Selects the applicable policy for a report, instantiates
its ordered step chain at submit, advances step-by-step, and derives the report
status from the steps. No policy ⇒ a single generic open step, so the legacy
single-approver flow is unchanged.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expense import ExpenseReport
from app.models.expense_approval import (
    KIND_APPROVER,
    KIND_FINANCE_FINAL,
    STEP_APPROVED,
    STEP_PENDING,
    STEP_REJECTED,
    STEP_SKIPPED,
    ExpenseApprovalPolicy,
    ExpenseApprovalStep,
)


class PolicyConcurrencyError(Exception):
    """The policy was modified concurrently (version mismatch) → 409."""


def approver_ids_of(policy: ExpenseApprovalPolicy | None) -> list[str]:
    if policy is None or not policy.approver_ids:
        return []
    try:
        val = json.loads(policy.approver_ids)
        return [str(x) for x in val] if isinstance(val, list) else []
    except (ValueError, TypeError):
        return []


def _report_amount_eur(report: ExpenseReport) -> Decimal:
    return Decimal(report.total_eur if report.total_eur is not None else (report.total or 0))


def matches(policy: ExpenseApprovalPolicy, report: ExpenseReport) -> bool:
    """Every SET criterion must match. Currently: minimum report EUR amount."""
    if policy.min_amount is not None and _report_amount_eur(report) < Decimal(policy.min_amount):
        return False
    return True


async def evaluate(
    db: AsyncSession, org_id: str, report: ExpenseReport
) -> ExpenseApprovalPolicy | None:
    """The first active, fully-matching policy by (priority, created_at)."""
    policies = list(
        await db.scalars(
            select(ExpenseApprovalPolicy)
            .where(ExpenseApprovalPolicy.org_id == org_id, ExpenseApprovalPolicy.active.is_(True))
            .order_by(ExpenseApprovalPolicy.priority.asc(), ExpenseApprovalPolicy.created_at.asc())
        )
    )
    for p in policies:
        if matches(p, report):
            return p
    return None


async def _emails_for(db: AsyncSession, org_id: str, user_ids: list[str]) -> dict[str, str]:
    # B1.5: approvers are org MEMBERS, resolved via memberships — an approver
    # currently switched into another org must still resolve to an e-mail here
    # (`users.org_id` is only the active-org pointer, never a membership test).
    from sqlalchemy import and_

    from app.models.membership import Membership
    from app.models.user import User

    if not user_ids:
        return {}
    rows = await db.scalars(
        select(User)
        .join(Membership, and_(Membership.user_id == User.id, Membership.org_id == org_id))
        .where(User.id.in_(user_ids))
    )
    return {u.id: u.email for u in rows}


async def build_chain(
    db: AsyncSession, org_id: str, report: ExpenseReport, policy: ExpenseApprovalPolicy | None
) -> list[ExpenseApprovalStep]:
    """Instantiate the report's approval chain. One step per policy approver (in
    order), optionally a finance-final step. No policy / empty chain ⇒ one generic
    OPEN step (approver_id None = any expense approver). Flushes; caller commits."""
    steps: list[ExpenseApprovalStep] = []
    seq = 0
    if policy is not None:
        ids = approver_ids_of(policy)
        emails = await _emails_for(db, org_id, [*ids, policy.finance_approver_id or ""])
        for uid in ids:
            steps.append(
                ExpenseApprovalStep(
                    org_id=org_id,
                    report_id=report.id,
                    policy_id=policy.id,
                    seq=seq,
                    kind=KIND_APPROVER,
                    approver_id=uid,
                    approver_email=emails.get(uid),
                    status=STEP_PENDING,
                )
            )
            seq += 1
        if policy.finance_final:
            steps.append(
                ExpenseApprovalStep(
                    org_id=org_id,
                    report_id=report.id,
                    policy_id=policy.id,
                    seq=seq,
                    kind=KIND_FINANCE_FINAL,
                    approver_id=policy.finance_approver_id,
                    approver_email=emails.get(policy.finance_approver_id or ""),
                    status=STEP_PENDING,
                )
            )
            seq += 1
    if not steps:
        steps.append(
            ExpenseApprovalStep(
                org_id=org_id,
                report_id=report.id,
                policy_id=policy.id if policy else None,
                seq=0,
                kind=KIND_APPROVER,
                approver_id=None,  # open — any expense approver
                status=STEP_PENDING,
            )
        )
    for s in steps:
        db.add(s)
    await db.flush()
    return steps


async def steps_for(db: AsyncSession, org_id: str, report_id: str) -> list[ExpenseApprovalStep]:
    return list(
        await db.scalars(
            select(ExpenseApprovalStep)
            .where(
                ExpenseApprovalStep.org_id == org_id,
                ExpenseApprovalStep.report_id == report_id,
            )
            .order_by(ExpenseApprovalStep.seq.asc())
        )
    )


async def delete_chain(db: AsyncSession, org_id: str, report_id: str) -> None:
    for s in await steps_for(db, org_id, report_id):
        await db.delete(s)
    await db.flush()


def pending_step(steps: list[ExpenseApprovalStep]) -> ExpenseApprovalStep | None:
    """The current step = the lowest-seq step still pending."""
    for s in sorted(steps, key=lambda x: x.seq):
        if s.status == STEP_PENDING:
            return s
    return None


def chain_state(steps: list[ExpenseApprovalStep]) -> str:
    """Derive the report status from the steps (mirrors the invoice engine)."""
    if any(s.status == STEP_REJECTED for s in steps):
        return "rejected"
    if not any(s.status == STEP_PENDING for s in steps):
        return "approved"  # every step decided, none rejected
    if any(s.status == STEP_APPROVED for s in steps):
        return "partially_approved"
    return "submitted"


def is_assigned_approver(step: ExpenseApprovalStep, user) -> bool:
    """An open step (approver_id None) accepts any expense approver; a named step
    only its assignee (platform admins may always act)."""
    if step.approver_id is None:
        return True
    if getattr(user, "is_platform_admin", False):
        return True
    return step.approver_id == user.id


def skip_pending(steps: list[ExpenseApprovalStep]) -> None:
    for s in steps:
        if s.status == STEP_PENDING:
            s.status = STEP_SKIPPED


def decide_step(step: ExpenseApprovalStep, user, *, approved: bool, note: str | None) -> None:
    step.status = STEP_APPROVED if approved else STEP_REJECTED
    step.decided_by = user.id
    step.decided_by_email = user.email
    step.decided_at = datetime.now(UTC)
    step.note = note
