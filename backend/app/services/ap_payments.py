"""Accounts-payable payment ledger (Phase 13).

The AP mirror of `services.payments`: the history behind a supplier
`invoices.amount_paid`. `amount_paid` stays the authoritative running-total CACHE
every read path uses; this service keeps it in sync with the ledger and exposes the
per-entry history. `set_cumulative` records the CHANGE as a signed ledger entry
(a downward adjustment writes a negative correction), so
SUM(supplier_payments.amount) == amount_paid always holds.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import money
from app.models.invoice import Invoice
from app.models.supplier_payment import SupplierPayment

_ZERO = Decimal("0")


async def list_for(db: AsyncSession, org_id: str, invoice_id: str) -> list[SupplierPayment]:
    """The payment history for one supplier invoice, oldest first."""
    return list(
        await db.scalars(
            select(SupplierPayment)
            .where(SupplierPayment.org_id == org_id, SupplierPayment.invoice_id == invoice_id)
            .order_by(SupplierPayment.paid_on.asc(), SupplierPayment.created_at.asc())
        )
    )


def _derive_paid_date(new_total: Decimal, total: Decimal, when: date | None) -> date | None:
    """A settlement date when fully paid; the supplied date for a partial payment;
    None when nothing is paid."""
    if new_total >= total and total > 0:
        return when or date.today()
    return when if new_total > 0 else None


async def set_cumulative(
    db: AsyncSession,
    org_id: str,
    inv: Invoice,
    *,
    new_total: Decimal,
    paid_date: date | None = None,
    method: str = "bank_transfer",
    reference: str | None = None,
    note: str | None = None,
) -> SupplierPayment | None:
    """Set `inv.amount_paid` to `new_total`, recording the delta as a signed ledger
    entry and refreshing the derived `paid_date`. Returns the new entry, or None when
    the total is unchanged. The caller enforces the overpay cap (`new_total <=
    total`)."""
    old = Decimal(inv.amount_paid or _ZERO)
    new_total = money.q2(Decimal(new_total))
    delta = money.q2(new_total - old)
    entry: SupplierPayment | None = None
    if delta != _ZERO:
        entry = SupplierPayment(
            org_id=org_id,
            invoice_id=inv.id,
            amount=delta,
            paid_on=paid_date or date.today(),
            method=method,
            reference=reference,
            note=note,
        )
        db.add(entry)
    inv.amount_paid = new_total
    inv.paid_date = _derive_paid_date(new_total, money.q2(Decimal(inv.total)), paid_date)
    return entry
