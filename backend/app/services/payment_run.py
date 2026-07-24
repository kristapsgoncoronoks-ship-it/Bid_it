"""Supplier payment runs (Phase 14).

The AP counterpart of `services.reimbursement`: group scheduled-for-payment
supplier invoices into a payout run, mark the run paid together (which writes an AP
payment-ledger entry settling each invoice in full and advances it to `paid`), and
export it as a bank file. A paid run is a reconciliation debit target. Tenant-
scoped; pure DB + logic, no HTTP (the route maps errors to status codes).
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.money import q2
from app.models.invoice import Invoice, InvoiceStatus, WorkflowState
from app.models.payment_run import RUN_CANCELLED, RUN_OPEN, RUN_PAID, PaymentRun
from app.services import ap_payments, invoice_workflow


class PaymentRunError(Exception):
    """A payment-run precondition failed (bad state / not eligible)."""


def eur_of(inv: Invoice) -> Decimal:
    return q2(Decimal(inv.total_eur if inv.total_eur is not None else (inv.total or 0)))


async def payable_invoices(db: AsyncSession, org_id: str) -> list[Invoice]:
    """Scheduled-for-payment invoices not yet in a run — the run's candidate pool."""
    return list(
        await db.scalars(
            select(Invoice)
            .where(
                Invoice.org_id == org_id,
                Invoice.workflow_state == WorkflowState.scheduled_for_payment,
                Invoice.payment_run_id.is_(None),
            )
            .options(selectinload(Invoice.vendor))
            .order_by(Invoice.due_date.asc())
        )
    )


async def run_invoices(db: AsyncSession, org_id: str, run_id: str) -> list[Invoice]:
    """Invoices linked to a run, ordered for a readable payout file."""
    return list(
        await db.scalars(
            select(Invoice)
            .where(Invoice.org_id == org_id, Invoice.payment_run_id == run_id)
            .options(selectinload(Invoice.vendor))
            .order_by(Invoice.invoice_number.asc())
        )
    )


async def create_run(
    db: AsyncSession,
    org_id: str,
    invoice_ids: list[str],
    *,
    method: str,
    note: str | None,
    created_by: str | None,
) -> PaymentRun:
    """Group scheduled-for-payment, un-run invoices into an OPEN run. Raises
    PaymentRunError if any invoice is missing, not scheduled for payment, or already
    in a run. Flushes; caller commits."""
    if not invoice_ids:
        raise PaymentRunError("Select at least one scheduled invoice.")
    invoices = list(
        await db.scalars(
            select(Invoice).where(Invoice.org_id == org_id, Invoice.id.in_(invoice_ids))
        )
    )
    found = {i.id for i in invoices}
    missing = [iid for iid in invoice_ids if iid not in found]
    if missing:
        raise PaymentRunError(f"Invoice(s) not found: {', '.join(missing)}")
    for inv in invoices:
        if inv.workflow_state != WorkflowState.scheduled_for_payment:
            raise PaymentRunError(
                f"Invoice '{inv.invoice_number}' is {inv.workflow_state.value}; "
                "only a scheduled-for-payment invoice can be added to a run."
            )
        if inv.payment_run_id:
            raise PaymentRunError(f"Invoice '{inv.invoice_number}' is already in a run.")

    run = PaymentRun(
        org_id=org_id,
        method=method,
        note=note,
        created_by=created_by,
        status=RUN_OPEN,
        total_eur=q2(sum((eur_of(i) for i in invoices), Decimal("0"))),
    )
    db.add(run)
    await db.flush()
    for inv in invoices:
        inv.payment_run_id = run.id
    return run


async def mark_paid(
    db: AsyncSession,
    org_id: str,
    run: PaymentRun,
    *,
    reference: str | None,
    method: str | None = None,
) -> list[Invoice]:
    """Mark an open run paid: stamp it, then settle every linked invoice IN FULL via
    the AP payment ledger (stamped with the run id) and advance it to `paid`. Returns
    the affected invoices."""
    if run.status != RUN_OPEN:
        raise PaymentRunError(f"Run is {run.status}; only an open run can be paid.")
    invoices = await run_invoices(db, org_id, run.id)
    if not invoices:
        raise PaymentRunError("Run has no invoices to pay.")
    now = datetime.now(UTC)
    if method:
        run.method = method
    run.reference = reference
    run.status = RUN_PAID
    run.paid_at = now
    run.total_eur = q2(sum((eur_of(i) for i in invoices), Decimal("0")))
    run.version += 1
    for inv in invoices:
        await ap_payments.set_cumulative(
            db,
            org_id,
            inv,
            new_total=q2(Decimal(inv.total)),
            paid_date=now.date(),
            method=run.method,
            reference=reference,
            run_id=run.id,
        )
        # scheduled_for_payment → paid is a legal transition; sync the aging status.
        invoice_workflow.assert_transition(inv.workflow_state, WorkflowState.paid)
        inv.workflow_state = WorkflowState.paid
        inv.status = InvoiceStatus.paid
        inv.version = (inv.version or 0) + 1
    return invoices


async def cancel_run(db: AsyncSession, org_id: str, run: PaymentRun) -> list[Invoice]:
    """Cancel an open run: unlink its invoices (they return to the scheduled pool).
    A paid run cannot be cancelled."""
    if run.status != RUN_OPEN:
        raise PaymentRunError(f"Run is {run.status}; only an open run can be cancelled.")
    invoices = await run_invoices(db, org_id, run.id)
    for inv in invoices:
        inv.payment_run_id = None
    run.status = RUN_CANCELLED
    run.version += 1
    return invoices


def export_csv(run: PaymentRun, invoices: list[Invoice]) -> str:
    """A bank-friendly CSV of the run's payments (one row per invoice)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["run_reference", "invoice_number", "amount", "currency", "amount_eur", "method"])
    for inv in invoices:
        w.writerow(
            [
                run.reference or run.id,
                inv.invoice_number,
                f"{q2(Decimal(inv.total or 0))}",
                inv.currency,
                f"{eur_of(inv)}",
                run.method,
            ]
        )
    return buf.getvalue()
