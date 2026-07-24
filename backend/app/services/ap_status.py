"""Derived accounts-payable status for a received supplier invoice.

The AP mirror of `issued_status`: payment status is never stored — it is computed
from `total`, `amount_paid`, and `due_date`, so it can never drift out of sync with
the amounts. Reused by the invoice serializer, the payment route, and any AP report.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.core import money
from app.models.invoice import Invoice

_ZERO = Decimal("0")

# Canonical AP settlement statuses (distinct from the approval workflow_state).
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


def outstanding_of(inv: Invoice) -> Decimal:
    """Amount still owed to the supplier (never negative)."""
    return money.q2(max(_ZERO, Decimal(inv.total) - Decimal(inv.amount_paid or _ZERO)))


def status_of(inv: Invoice, today: date | None = None) -> str:
    today = today or date.today()
    total = money.q2(Decimal(inv.total))
    paid = Decimal(inv.amount_paid or _ZERO)
    if total > _ZERO and paid >= total:
        return PAID
    if inv.due_date is not None and inv.due_date < today:
        return OVERDUE
    if paid > _ZERO:
        return PARTIAL
    return OPEN


def days_overdue_of(inv: Invoice, today: date | None = None) -> int:
    """Whole days past the due date (0 if not yet due, no due date, or fully paid)."""
    today = today or date.today()
    if inv.due_date is None or inv.due_date >= today:
        return 0
    if (
        Decimal(inv.amount_paid or _ZERO) >= money.q2(Decimal(inv.total))
        and Decimal(inv.total) > _ZERO
    ):
        return 0
    return (today - inv.due_date).days
