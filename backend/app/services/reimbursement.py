"""Expense reimbursement — payout batches (Phase 09).

Turns the one-way "reimbursed" status flip into a real payout: group APPROVED
expense reports into a batch, mark the batch paid together with a shared method +
reference (which flips each report to `reimbursed` and stamps its payment
metadata), and export the batch as a bank/payroll file. Tenant-scoped; pure DB +
logic, no HTTP (the route maps validation errors to status codes).
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import q2
from app.models.expense import (
    BATCH_CANCELLED,
    BATCH_OPEN,
    BATCH_PAID,
    ExpenseReport,
    ReimbursementBatch,
)


class ReimbursementError(Exception):
    """A reimbursement precondition failed (bad state / not eligible)."""


def eur_of(r: ExpenseReport) -> Decimal:
    return q2(Decimal(r.total_eur if r.total_eur is not None else (r.total or 0)))


async def batch_reports(db: AsyncSession, org_id: str, batch_id: str) -> list[ExpenseReport]:
    """Reports linked to a batch, ordered by employee for a readable payout file."""
    return list(
        await db.scalars(
            select(ExpenseReport)
            .where(ExpenseReport.org_id == org_id, ExpenseReport.payout_batch_id == batch_id)
            .order_by(ExpenseReport.employee_name.asc())
        )
    )


async def create_batch(
    db: AsyncSession,
    org_id: str,
    report_ids: list[str],
    *,
    method: str,
    note: str | None,
    created_by: str | None,
) -> ReimbursementBatch:
    """Group approved, un-batched reports into an OPEN payout batch. Raises
    ReimbursementError if any report is missing, not approved, or already batched.
    Flushes; caller commits."""
    if not report_ids:
        raise ReimbursementError("Select at least one approved report.")
    reports = list(
        await db.scalars(
            select(ExpenseReport).where(
                ExpenseReport.org_id == org_id, ExpenseReport.id.in_(report_ids)
            )
        )
    )
    found = {r.id for r in reports}
    missing = [rid for rid in report_ids if rid not in found]
    if missing:
        raise ReimbursementError(f"Report(s) not found: {', '.join(missing)}")
    for r in reports:
        if r.status != "approved":
            raise ReimbursementError(
                f"Report '{r.title}' is {r.status}; only approved reports can be paid."
            )
        if r.payout_batch_id:
            raise ReimbursementError(f"Report '{r.title}' is already in a payout batch.")

    batch = ReimbursementBatch(
        org_id=org_id,
        method=method,
        note=note,
        created_by=created_by,
        status=BATCH_OPEN,
        total_eur=q2(sum((eur_of(r) for r in reports), Decimal("0"))),
    )
    db.add(batch)
    await db.flush()
    for r in reports:
        r.payout_batch_id = batch.id
    return batch


async def mark_paid(
    db: AsyncSession,
    org_id: str,
    batch: ReimbursementBatch,
    *,
    reference: str | None,
    method: str | None = None,
) -> list[ExpenseReport]:
    """Mark an open batch paid: stamp it, then flip every linked report to
    `reimbursed` with the payment metadata. Returns the affected reports."""
    if batch.status != BATCH_OPEN:
        raise ReimbursementError(f"Batch is {batch.status}; only an open batch can be paid.")
    reports = await batch_reports(db, org_id, batch.id)
    if not reports:
        raise ReimbursementError("Batch has no reports to pay.")
    now = datetime.now(UTC)
    if method:
        batch.method = method
    batch.reference = reference
    batch.status = BATCH_PAID
    batch.paid_at = now
    batch.total_eur = q2(sum((eur_of(r) for r in reports), Decimal("0")))
    batch.version += 1
    for r in reports:
        r.status = "reimbursed"
        r.reimbursed_at = now
        r.payment_method = batch.method
        r.payment_reference = reference
    return reports


async def cancel_batch(
    db: AsyncSession, org_id: str, batch: ReimbursementBatch
) -> list[ExpenseReport]:
    """Cancel an open batch: unlink its reports (they return to the payable pool).
    A paid batch cannot be cancelled."""
    if batch.status != BATCH_OPEN:
        raise ReimbursementError(f"Batch is {batch.status}; only an open batch can be cancelled.")
    reports = await batch_reports(db, org_id, batch.id)
    for r in reports:
        r.payout_batch_id = None
    batch.status = BATCH_CANCELLED
    batch.version += 1
    return reports


def export_csv(batch: ReimbursementBatch, reports: list[ExpenseReport]) -> str:
    """A bank/payroll-friendly CSV of the batch's payouts (one row per report)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["batch_reference", "employee", "report", "amount", "currency", "amount_eur", "method"]
    )
    for r in reports:
        w.writerow(
            [
                batch.reference or batch.id,
                r.employee_name,
                r.title,
                f"{q2(Decimal(r.total or 0))}",
                r.currency,
                f"{eur_of(r)}",
                batch.method,
            ]
        )
    return buf.getvalue()
