"""Derived accounts-receivable status for an issued invoice.

Payment status is never stored — it is computed from `total`, `amount_paid`,
and `due_date` so it can never drift out of sync with the amounts. Defined once
here and reused by the list/detail serializer and every report.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.core import money
from app.models.issued_invoice import IssuedInvoice

_ZERO = Decimal("0")

# Canonical statuses. `partial` is a distinct state but rolls up under "not paid"
# for the paid/unpaid/overdue view.
PAID = "paid"
PARTIAL = "partial"
OPEN = "open"
OVERDUE = "overdue"

STATUS_LABELS = {
    PAID: "Paid",
    PARTIAL: "Partially paid",
    OPEN: "Open",
    OVERDUE: "Overdue",
}


def outstanding_of(inv: IssuedInvoice) -> Decimal:
    """Amount still owed (never negative)."""
    return money.q2(max(_ZERO, Decimal(inv.total) - Decimal(inv.amount_paid or _ZERO)))


def status_of(inv: IssuedInvoice, today: date | None = None) -> str:
    today = today or date.today()
    paid = Decimal(inv.amount_paid or _ZERO)
    total = Decimal(inv.total)
    if total > _ZERO and paid >= total:
        return PAID
    if inv.due_date is not None and inv.due_date < today:
        return OVERDUE
    if paid > _ZERO:
        return PARTIAL
    return OPEN
