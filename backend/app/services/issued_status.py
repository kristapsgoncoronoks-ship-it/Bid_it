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
VOID = "void"
# Lifecycle-driven states (INV-3).
DRAFT = "draft"
APPROVED = "approved"
SENT = "sent"
VIEWED = "viewed"
DISPUTED = "disputed"
WRITTEN_OFF = "written_off"

STATUS_LABELS = {
    DRAFT: "Draft",
    APPROVED: "Approved",
    SENT: "Sent",
    VIEWED: "Viewed",
    PAID: "Paid",
    PARTIAL: "Partially paid",
    OPEN: "Open",
    OVERDUE: "Overdue",
    CREDITED: "Credited",
    CREDIT_NOTE: "Credit note",
    DISPUTED: "Disputed",
    WRITTEN_OFF: "Written off",
    VOID: "Cancelled",
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
    """Amount still owed (never negative). A credit note owes nothing; a written-off
    invoice (bad debt) and a never-issued draft/approved/cancelled document owe
    nothing either — only a live receivable accrues an outstanding balance."""
    if is_credit_note(inv):
        return money.q2(_ZERO)
    lifecycle = getattr(inv, "lifecycle", "issued") or "issued"
    if lifecycle in ("written_off", "cancelled", "draft", "approved"):
        return money.q2(_ZERO)
    return money.q2(max(_ZERO, effective_total(inv) - Decimal(inv.amount_paid or _ZERO)))


def ar_status_of(inv: IssuedInvoice, today: date | None = None) -> str:
    """The pure accounts-receivable status (draft/approved/void/written_off/
    disputed/credit_note lifecycle overrides, then paid/partial/open/overdue/
    credited). Delivery state (sent/viewed) is NOT folded in — reports bucket on
    this so a merely-delivered invoice still counts as an OPEN receivable."""
    today = today or date.today()
    # Stored lifecycle states take precedence over the derived AR status.
    lifecycle = getattr(inv, "lifecycle", "issued") or "issued"
    if lifecycle == "draft":
        return DRAFT
    if lifecycle == "approved":
        return APPROVED
    if lifecycle == "cancelled" or getattr(inv, "voided_at", None) is not None:
        return VOID  # cancelled — no longer a receivable
    if lifecycle == "written_off":
        return WRITTEN_OFF
    if lifecycle == "disputed":
        return DISPUTED
    if is_credit_note(inv):
        return CREDIT_NOTE
    # lifecycle == 'issued' → the AR-derived status.
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


def status_of(inv: IssuedInvoice, today: date | None = None) -> str:
    """The DISPLAY status: the AR status, refining a still-open receivable by its
    delivery state (viewed > sent > open) for the list/detail UI."""
    base = ar_status_of(inv, today)
    if base != OPEN:
        return base
    if getattr(inv, "viewed_at", None) is not None:
        return VIEWED
    if getattr(inv, "sent_at", None) is not None:
        return SENT
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
