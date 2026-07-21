"""Accounts-receivable payment ledger (Slice 3c).

The ledger is the history behind `issued_invoices.amount_paid`. `amount_paid`
stays the authoritative running-total CACHE every read path already uses; this
service keeps it in sync with the ledger and exposes the per-entry history.

`set_cumulative` preserves the existing "set the cumulative amount paid" API
(the frontend sends the new total): it records the CHANGE as a signed ledger
entry and refreshes the derived cache, so SUM(payments.amount) == amount_paid
always holds.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import money
from app.models.issued_invoice import IssuedInvoice
from app.models.payment import Payment

_ZERO = Decimal("0")


async def list_for(db: AsyncSession, org_id: str, invoice_id: str) -> list[Payment]:
    """The payment history for one issued invoice, oldest first."""
    return list(
        await db.scalars(
            select(Payment)
            .where(Payment.org_id == org_id, Payment.issued_invoice_id == invoice_id)
            .order_by(Payment.paid_on.asc(), Payment.created_at.asc())
        )
    )


def _derive_paid_date(new_total: Decimal, effective: Decimal, when: date | None) -> date | None:
    """Same rule the route used before the ledger: a settlement date when fully
    paid; the supplied date for a partial payment; None when nothing is paid."""
    if new_total >= effective and effective > 0:
        return when or date.today()
    return when if new_total > 0 else None


async def set_cumulative(
    db: AsyncSession,
    org_id: str,
    inv: IssuedInvoice,
    *,
    new_total: Decimal,
    effective: Decimal,
    paid_date: date | None = None,
    method: str = "bank_transfer",
    reference: str | None = None,
    note: str | None = None,
) -> Payment | None:
    """Set `inv.amount_paid` to `new_total`, recording the delta as a ledger entry
    and refreshing the derived `paid_date`. Returns the new entry, or None when the
    total is unchanged. The caller enforces the overpay cap (`new_total <=
    effective`)."""
    old = Decimal(inv.amount_paid or _ZERO)
    new_total = money.q2(Decimal(new_total))
    delta = money.q2(new_total - old)
    entry: Payment | None = None
    if delta != _ZERO:
        entry = Payment(
            org_id=org_id,
            issued_invoice_id=inv.id,
            amount=delta,
            paid_on=paid_date or date.today(),
            method=method,
            reference=reference,
            note=note,
        )
        db.add(entry)
    inv.amount_paid = new_total
    inv.paid_date = _derive_paid_date(new_total, effective, paid_date)
    return entry


async def backfill_ledger(db: AsyncSession, org_id: str) -> int:
    """Seed a 'migrated' ledger entry for any settled issued invoice that has none
    yet (for the demo seed or a store predating the ledger). Idempotent; commits
    once. Returns how many entries were created."""
    invoices = list(
        await db.scalars(
            select(IssuedInvoice).where(
                IssuedInvoice.org_id == org_id, IssuedInvoice.amount_paid != 0
            )
        )
    )
    made = 0
    for inv in invoices:
        has_any = await db.scalar(
            select(func.count(Payment.id)).where(
                Payment.org_id == org_id, Payment.issued_invoice_id == inv.id
            )
        )
        if has_any:
            continue
        db.add(
            Payment(
                org_id=org_id,
                issued_invoice_id=inv.id,
                amount=money.q2(Decimal(inv.amount_paid)),
                paid_on=inv.paid_date or inv.issue_date,
                method="migrated",
            )
        )
        made += 1
    if made:
        await db.commit()
    return made
