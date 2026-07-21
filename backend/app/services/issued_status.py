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
# for the paid/unpaid/overdue view. A CREDIT NOTE is its own document (not a
# receivable); an invoice fully cancelled by credit notes reads as CREDITED.
PAID = "paid"
PARTIAL = "partial"
OPEN = "open"
OVERDUE = "overdue"
CREDITED = "credited"
CREDIT_NOTE = "credit_note"

STATUS_LABELS = {
    PAID: "Paid",
    PARTIAL: "Partially paid",
    OPEN: "Open",
    OVERDUE: "Overdue",
    CREDITED: "Credited",
    CREDIT_NOTE: "Credit note",
}


def is_credit_note(inv: IssuedInvoice) -> bool:
    return getattr(inv, "doc_type", "invoice") == "credit_note"


def effective_total(inv: IssuedInvoice) -> Decimal:
    """The invoice total net of any credit notes applied against it. For a credit
    note itself this is just its own total (it is never a receivable)."""
    if is_credit_note(inv):
        return money.q2(Decimal(inv.total))
    return money.q2(Decimal(inv.total) - Decimal(getattr(inv, "credited_total", None) or _ZERO))


def outstanding_of(inv: IssuedInvoice) -> Decimal:
    """Amount still owed (never negative). A credit note owes nothing."""
    if is_credit_note(inv):
        return money.q2(_ZERO)
    return money.q2(max(_ZERO, effective_total(inv) - Decimal(inv.amount_paid or _ZERO)))


def status_of(inv: IssuedInvoice, today: date | None = None) -> str:
    today = today or date.today()
    if is_credit_note(inv):
        return CREDIT_NOTE
    paid = Decimal(inv.amount_paid or _ZERO)
    eff = effective_total(inv)
    credited = Decimal(getattr(inv, "credited_total", None) or _ZERO)
    # Fully cancelled by credit notes (and nothing left to collect).
    if credited > _ZERO and eff <= _ZERO and paid <= _ZERO:
        return CREDITED
    if eff > _ZERO and paid >= eff:
        return PAID
    if eff <= _ZERO and paid > _ZERO:
        return PAID  # over-credited but already settled — treat as done
    if inv.due_date is not None and inv.due_date < today:
        return OVERDUE
    if paid > _ZERO:
        return PARTIAL
    return OPEN


def days_overdue_of(inv: IssuedInvoice, today: date | None = None) -> int:
    """Whole days past the due date (0 if not yet due or no due date)."""
    today = today or date.today()
    if inv.due_date is None or inv.due_date >= today:
        return 0
    return (today - inv.due_date).days


def penalty_of(inv: IssuedInvoice, today: date | None = None) -> Decimal:
    """Accrued late-payment interest (ADVISORY — never added to the stored total).

    Only invoices that CARRY a `penalty_rate` accrue. Simple interest on the
    still-owed balance: outstanding × rate%/year × days_overdue/365 (ACT/365).
    Zero unless the invoice is overdue with a balance and a rate set.
    """
    rate = inv.penalty_rate
    if not rate or Decimal(rate) <= _ZERO:
        return _ZERO
    outstanding = outstanding_of(inv)
    days = days_overdue_of(inv, today)
    if outstanding <= _ZERO or days <= 0:
        return _ZERO
    return money.q2(outstanding * Decimal(rate) / Decimal(100) * Decimal(days) / Decimal(365))
