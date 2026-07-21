"""Incoming-payment receipts + allocation (Slice 5c).

A receipt is money received (one bank transfer); allocating it to an issued
invoice records a `payments` ledger entry stamped with the receipt id, so the
invoice's `amount_paid` stays the single source of truth. Tenant-scoped.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import money
from app.models.issued_invoice import IssuedInvoice
from app.models.payment import Payment
from app.models.receipt import Receipt
from app.services import issued_status, payments

_ZERO = Decimal("0")


class ReceiptError(Exception):
    """Business-rule violation (over-allocation, bad target, credit note)."""


async def create(
    db: AsyncSession,
    org_id: str,
    *,
    amount: Decimal,
    received_on: date,
    method: str = "bank_transfer",
    reference: str | None = None,
    note: str | None = None,
) -> Receipt:
    r = Receipt(
        org_id=org_id,
        amount=money.q2(Decimal(amount)),
        received_on=received_on,
        method=method,
        reference=reference,
        note=note,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def get(db: AsyncSession, org_id: str, receipt_id: str) -> Receipt | None:
    return await db.scalar(
        select(Receipt).where(Receipt.org_id == org_id, Receipt.id == receipt_id)
    )


async def list_receipts(db: AsyncSession, org_id: str) -> list[Receipt]:
    return list(
        await db.scalars(
            select(Receipt)
            .where(Receipt.org_id == org_id)
            .order_by(Receipt.received_on.desc(), Receipt.created_at.desc())
        )
    )


async def allocated_total(db: AsyncSession, org_id: str, receipt_id: str) -> Decimal:
    total = await db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.org_id == org_id, Payment.receipt_id == receipt_id
        )
    )
    return money.q2(Decimal(total or 0))


async def unallocated(db: AsyncSession, org_id: str, receipt: Receipt) -> Decimal:
    return money.q2(Decimal(receipt.amount) - await allocated_total(db, org_id, receipt.id))


async def allocations(db: AsyncSession, org_id: str, receipt_id: str) -> list[Payment]:
    return list(
        await db.scalars(
            select(Payment)
            .where(Payment.org_id == org_id, Payment.receipt_id == receipt_id)
            .order_by(Payment.created_at.asc())
        )
    )


async def allocate(
    db: AsyncSession, org_id: str, receipt: Receipt, invoice: IssuedInvoice, amount: Decimal
) -> Payment:
    """Allocate `amount` of a receipt to an issued invoice. Capped by BOTH the
    receipt's unallocated balance and the invoice's outstanding amount. Records a
    ledger entry + refreshes the invoice cache; commits."""
    amount = money.q2(Decimal(amount))
    if amount <= _ZERO:
        raise ReceiptError("allocation amount must be positive")
    if issued_status.is_credit_note(invoice):
        raise ReceiptError("a credit note is not a receivable — no payment applies")

    free = await unallocated(db, org_id, receipt)
    if amount > free:
        raise ReceiptError(f"amount exceeds the receipt's unallocated balance ({free})")
    outstanding = issued_status.outstanding_of(invoice)
    if amount > outstanding:
        raise ReceiptError(f"amount exceeds the invoice's outstanding balance ({outstanding})")

    entry = await payments.add_receipt_allocation(
        db,
        org_id,
        invoice,
        amount=amount,
        effective=issued_status.effective_total(invoice),
        receipt_id=receipt.id,
        paid_on=receipt.received_on,
        method=receipt.method,
        reference=receipt.reference,
    )
    await db.commit()
    return entry
