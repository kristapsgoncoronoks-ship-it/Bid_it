"""Append-only cash commands. Caller locks the invoice and commits all effects."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models.issued_invoice import IssuedInvoice
from app.models.payment import Payment
from app.schemas.issued import CashPaymentCreate, PaymentReversalCreate
from app.services import audit, issued_status, payments, webhooks


async def lookup(
    db: AsyncSession, org_id: str, key: str, expected: str | None = None
) -> Payment | None:
    row = await db.scalar(
        select(Payment).where(Payment.org_id == org_id, Payment.command_key == key)
    )
    if row is not None and expected is not None and row.command_hash != expected:
        raise ConflictError(
            "This payment command was used with different inputs.", code="command_payload_conflict"
        )
    return row


def check_balance(inv: IssuedInvoice, currency: str, expected: Decimal) -> None:
    if inv.currency != currency:
        raise ConflictError(
            "Payment currency differs from the invoice. Refresh the invoice.",
            code="payment_currency_mismatch",
        )
    if issued_status.outstanding_of(inv) != expected:
        raise ConflictError(
            "The outstanding balance changed. Refresh and review before recording cash.",
            code="payment_balance_changed",
        )


async def effects(db: AsyncSession, inv: IssuedInvoice, entry: Payment) -> None:
    await db.flush()
    reversal = entry.reversal_of_id is not None
    event = await audit.record(
        db,
        audit.A.ISSUED_PAYMENT_REVERSED if reversal else audit.A.ISSUED_PAYMENT,
        target_type="issued_invoice",
        target_id=inv.id,
        meta={
            "number": inv.number,
            "payment_id": entry.id,
            "amount": str(entry.amount),
            "amount_paid": str(inv.amount_paid),
            "currency": inv.currency,
            "reversal_of_id": entry.reversal_of_id,
            "reason": entry.note if reversal else None,
        },
    )
    if event is None:
        raise RuntimeError("A cash command requires a committed audit event")
    await webhooks.emit(
        db,
        inv.org_id,
        "issued.payment",
        {
            "id": inv.id,
            "number": inv.number,
            "payment_id": entry.id,
            "amount": str(entry.amount),
            "amount_paid": str(inv.amount_paid),
            "outstanding": str(issued_status.outstanding_of(inv)),
            "status": issued_status.status_of(inv),
            "currency": inv.currency,
            "reversal_of_id": entry.reversal_of_id,
        },
        idempotency_key=f"payment:{entry.id}",
        strict=True,
    )


async def append(
    db: AsyncSession, inv: IssuedInvoice, body: CashPaymentCreate, key: str, digest: str
) -> Payment:
    check_balance(inv, body.currency, body.expected_outstanding)
    if body.amount > issued_status.outstanding_of(inv):
        raise ConflictError(
            "Payment exceeds the remaining amount owed after credits.", code="payment_overpay"
        )
    entry = await payments.set_cumulative(
        db,
        inv.org_id,
        inv,
        new_total=Decimal(inv.amount_paid or 0) + body.amount,
        effective=issued_status.effective_total(inv),
        paid_date=body.paid_on,
        method=body.method,
        reference=body.reference,
    )
    assert entry is not None  # positive, exact cents validated by schema
    entry.command_key, entry.command_hash = key, digest
    await effects(db, inv, entry)
    return entry


async def reverse(
    db: AsyncSession,
    inv: IssuedInvoice,
    payment_id: str,
    body: PaymentReversalCreate,
    key: str,
    digest: str,
) -> Payment:
    check_balance(inv, body.currency, body.expected_outstanding)
    original = await db.scalar(
        select(Payment).where(
            Payment.org_id == inv.org_id,
            Payment.issued_invoice_id == inv.id,
            Payment.id == payment_id,
        )
    )
    if original is None:
        raise ConflictError("Payment not found.", code="payment_not_found", status=404)
    if original.receipt_id is not None:
        raise ConflictError(
            "Reverse this allocation through its receipt or bank reconciliation workflow.",
            code="payment_receipt_linked",
        )
    if original.amount <= 0 or original.reversal_of_id is not None:
        raise ConflictError(
            "Only a positive direct payment can be reversed.", code="payment_not_reversible"
        )
    already = await db.scalar(
        select(Payment.id).where(
            Payment.org_id == inv.org_id,
            Payment.reversal_of_id == original.id,
        )
    )
    if already is not None:
        raise ConflictError(
            "This payment has already been reversed.", code="payment_already_reversed"
        )
    unexplained = await db.scalar(
        select(Payment.id)
        .where(
            Payment.org_id == inv.org_id,
            Payment.issued_invoice_id == inv.id,
            Payment.amount < 0,
            Payment.reversal_of_id.is_(None),
            Payment.receipt_id.is_(None),
        )
        .limit(1)
    )
    if unexplained is not None:
        raise ConflictError(
            "Historical unlinked corrections require review before reversing direct cash.",
            code="payment_history_review",
        )
    if Decimal(inv.amount_paid or 0) < original.amount:
        raise ConflictError(
            "Historical corrections require review before reversing this payment.",
            code="payment_history_review",
        )
    entry = await payments.set_cumulative(
        db,
        inv.org_id,
        inv,
        new_total=Decimal(inv.amount_paid) - original.amount,
        effective=issued_status.effective_total(inv),
        paid_date=inv.paid_date,
        method=original.method,
        reference=original.reference,
        note=body.reason,
    )
    assert entry is not None
    entry.paid_on = body.paid_on or date.today()
    entry.reversal_of_id = original.id
    entry.command_key, entry.command_hash = key, digest
    await effects(db, inv, entry)
    return entry
