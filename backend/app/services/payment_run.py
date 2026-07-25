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
from app.models.vendor import VENDOR_PROVISIONAL, Vendor
from app.services import ap_payments, invoice_workflow
from app.services import vendors as vendor_service


class PaymentRunError(Exception):
    """A payment-run precondition failed (bad state / not eligible)."""


async def _assert_vendors_payable(
    db: AsyncSession,
    org_id: str,
    invoices: list[Invoice],
    *,
    allow_provisional: bool,
) -> None:
    """The WO-2 fraud gate on the payout path. Refuses (naming the vendor):

    - a vendor with a PENDING protected-field change request — paying while a
      bank-detail change is in flight would either pay an account someone is
      actively trying to replace, or race the approval;
    - a PROVISIONAL vendor (bank/tax identity captured at creation, never
      independently verified) unless the caller explicitly confirmed it.

    Fail-closed: this runs at run CREATION and again at PAY time, because a
    change request can be filed between the two."""
    vendor_ids = sorted({inv.vendor_id for inv in invoices if inv.vendor_id})
    if not vendor_ids:
        return
    vendors = {
        v.id: v
        for v in await db.scalars(
            select(Vendor).where(Vendor.org_id == org_id, Vendor.id.in_(vendor_ids))
        )
    }
    pending = await vendor_service.pending_requests_for(db, org_id, vendor_ids)
    for vid in vendor_ids:
        vendor = vendors.get(vid)
        if vendor is None:
            continue
        if pending.get(vid):
            fields = ", ".join(sorted({r.field for r in pending[vid]}))
            raise PaymentRunError(
                f"Vendor '{vendor.name}' has a pending bank-detail change request "
                f"({fields}); approve or reject it before paying this vendor."
            )
        if vendor.status == VENDOR_PROVISIONAL and not allow_provisional:
            raise PaymentRunError(
                f"Vendor '{vendor.name}' is provisional (its bank/tax identity was "
                "captured but never verified); confirm it explicitly to include it "
                "in a payment run."
            )


def eur_of(inv: Invoice) -> Decimal:
    """The invoice's EUR amount for run totals and the SEPA bank file.

    Fail-CLOSED (WO-8): an EUR-currency invoice with no stamped `total_eur` is
    the identity conversion (rate 1 by convention — not a guess); a FOREIGN
    invoice with no stamped `total_eur` is REFUSED. The old fallback silently
    used the raw foreign total as if it were EUR, and the SEPA file then
    instructed the bank to pay e.g. EUR 1,000 for a 1,000-PLN invoice. A
    blocked run is recoverable; a mis-currencied payment is not."""
    if inv.total_eur is not None:
        return q2(Decimal(inv.total_eur))
    if (inv.currency or "EUR").upper() == "EUR":
        return q2(Decimal(inv.total or 0))
    raise PaymentRunError(
        f"Invoice '{inv.invoice_number}' is in {inv.currency} with no recorded EUR "
        f"conversion (no rate was available for {inv.currency}); refresh the ECB "
        "rates and re-register the invoice before paying it."
    )


def eur_or_none(inv: Invoice) -> Decimal | None:
    """Best-effort EUR figure for REPORTING surfaces (the CSV export): None when
    the invoice has no reliable EUR value — the row still shows its original
    amount + currency, and the EUR column stays honestly blank."""
    try:
        return eur_of(inv)
    except PaymentRunError:
        return None


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
    confirm_provisional: bool = False,
) -> PaymentRun:
    """Group scheduled-for-payment, un-run invoices into an OPEN run. Raises
    PaymentRunError if any invoice is missing, not scheduled for payment, or already
    in a run — or (WO-2) if a linked vendor has a pending bank-detail change, or is
    provisional and `confirm_provisional` was not set. Flushes; caller commits."""
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
    await _assert_vendors_payable(db, org_id, invoices, allow_provisional=confirm_provisional)

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
    # Re-check at pay time: a bank-detail change request filed AFTER the run was
    # created must still block the payout (provisional vendors were confirmed —
    # or refused — at creation, so only the pending-change gate re-runs here).
    await _assert_vendors_payable(db, org_id, invoices, allow_provisional=True)
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
        eur = eur_or_none(inv)
        w.writerow(
            [
                run.reference or run.id,
                inv.invoice_number,
                f"{q2(Decimal(inv.total or 0))}",
                inv.currency,
                f"{eur}" if eur is not None else "",
                run.method,
            ]
        )
    return buf.getvalue()
