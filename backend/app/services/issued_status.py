"""Derived accounts-receivable status for an issued invoice.

Payment status is never stored — it is computed from `total`, `amount_paid`,
and `due_date` so it can never drift out of sync with the amounts. Defined once
here and reused by the list/detail serializer and every report.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func

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


def _status_rules(inv, today: date, *, sql: bool = False, delivery: bool = True):
    """One ordered decision list for Python display and SQL selection.

    A generator preserves the original short-circuiting lifecycle behavior.
    SQL rounding adapts SQLite's representation to the stored cent precision;
    PostgreSQL Numeric and Python Decimal follow the same business decisions.
    """

    def value(name, default):
        attribute = getattr(inv, name, None)
        return func.coalesce(attribute, default) if sql else (attribute or default)

    def present(name):
        attribute = getattr(inv, name, None)
        return attribute.is_not(None) if sql else attribute is not None

    lifecycle = value("lifecycle", "issued")
    yield lifecycle == "draft", DRAFT
    yield lifecycle == "approved", APPROVED
    yield (lifecycle == "cancelled") | present("voided_at"), VOID
    yield lifecycle == "written_off", WRITTEN_OFF
    yield lifecycle == "disputed", DISPUTED
    yield is_credit_note(inv), CREDIT_NOTE
    paid = value("amount_paid", _ZERO)
    credited = value("credited_total", _ZERO)
    eff: Any
    if sql:
        eff = func.round(inv.total - credited, 2)
    else:
        paid, credited = Decimal(paid), Decimal(credited)
        eff = effective_total(inv)
    yield (credited > _ZERO) & (eff <= _ZERO) & (paid <= _ZERO), CREDITED
    yield ((eff > _ZERO) & (paid >= eff)) | ((eff <= _ZERO) & (paid > _ZERO)), PAID
    if sql:
        overdue = present("due_date") & (inv.due_date < today)
    else:
        overdue = inv.due_date is not None and inv.due_date < today
    yield overdue, OVERDUE
    yield paid > _ZERO, PARTIAL
    if delivery:
        yield present("viewed_at"), VIEWED
        yield present("sent_at"), SENT


def ar_status_of(inv: IssuedInvoice, today: date | None = None) -> str:
    """AR lifecycle/payment status, without delivery refinements."""
    for matches, status in _status_rules(inv, today or date.today(), delivery=False):
        if matches:
            return status
    return OPEN


def status_of(inv: IssuedInvoice, today: date | None = None) -> str:
    """Display status: delivery refines only an otherwise OPEN receivable."""
    for matches, status in _status_rules(inv, today or date.today()):
        if matches:
            return status
    return OPEN


def sql_status(today: date | None = None, *, delivery: bool = True):
    """The canonical status decisions compiled for filtering BEFORE a limit."""
    return case(
        *_status_rules(IssuedInvoice, today or date.today(), sql=True, delivery=delivery),
        else_=OPEN,
    )


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

    This is the TOTAL accrued since the due date — the right figure to SHOW
    ("this debt has cost you X"). It is the wrong figure to BILL once any of it
    has been billed already; use `billable_penalty_of` for that.
    """
    rate = inv.penalty_rate
    if not rate or Decimal(rate) <= _ZERO:
        return _ZERO
    outstanding = outstanding_of(inv)
    days = days_overdue_of(inv, today)
    if outstanding <= _ZERO or days <= 0:
        return _ZERO
    return money.q2(outstanding * Decimal(rate) / Decimal(100) * Decimal(days) / Decimal(365))


def unbilled_days_of(inv: IssuedInvoice, today: date | None = None) -> int:
    """Days of lateness NOT yet covered by a penalty invoice.

    Accrual starts at the due date, or at `interest_billed_through` once
    interest has been billed — whichever is later. Without this the second
    penalty invoice re-bills from the due date, so a debtor billed monthly pays
    month 1 again in month 2 and a third time in month 3.
    """
    today = today or date.today()
    if inv.due_date is None:
        return 0
    start = inv.due_date
    billed_through = getattr(inv, "interest_billed_through", None)
    if billed_through is not None and billed_through > start:
        start = billed_through
    if start >= today:
        return 0
    return (today - start).days


def billable_penalty_of(inv: IssuedInvoice, today: date | None = None) -> Decimal:
    """Interest that may still be CHARGED — the accrual not already invoiced.

    Same arithmetic as `penalty_of` over `unbilled_days_of` instead of the whole
    overdue span. Equal to `penalty_of` until the first penalty invoice, and
    zero immediately after one, which is the property that stops the same days
    being billed twice.
    """
    rate = inv.penalty_rate
    if not rate or Decimal(rate) <= _ZERO:
        return _ZERO
    outstanding = outstanding_of(inv)
    days = unbilled_days_of(inv, today)
    if outstanding <= _ZERO or days <= 0:
        return _ZERO
    return money.q2(outstanding * Decimal(rate) / Decimal(100) * Decimal(days) / Decimal(365))
