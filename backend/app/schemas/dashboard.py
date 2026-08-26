"""Wire shapes for the composed home dashboard (WO-16 / I1.1).

Sectioned by canonical surface; a section is ``null`` when the caller lacks that
surface's permission (or its module is off) — the SPA hides the card instead of
fanning out N calls and swallowing 403s. Every figure inside a section is a
verbatim projection of the owning service's output (ADR-0023: no forked math).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ApApprovalItem(BaseModel):
    """One supplier invoice whose current approval step awaits the caller."""

    invoice_id: str
    invoice_number: str
    vendor_name: str | None = None
    total: Decimal
    currency: str


class ApprovalsSection(BaseModel):
    """Everything awaiting the caller's decision. Sub-fields are ``null`` when
    the caller lacks that surface's own permission (or its module is off);
    ``total`` sums only the visible counts."""

    invoices: list[ApApprovalItem] | None = None  # INVOICE_APPROVE
    invoice_count: int | None = None
    expense_reports: int | None = None  # EXPENSE_APPROVE + expenses module
    payment_runs: int | None = None  # PAYMENT_WRITE (maker≠checker excluded)
    vendor_changes: int | None = None  # SETTINGS_MANAGE (own requests excluded)
    total: int


class CapturesSection(BaseModel):
    """The capture review queue roll-up (INVOICE_READ)."""

    pending: int
    low_confidence_fields: int


class PayablesSection(BaseModel):
    """AP due-soon/overdue roll-up — verbatim `ap_aging.DueSummary` (REPORT_READ).
    Counts span every currency; amounts are single-currency with the rest listed
    in `other_currencies`, never summed (§4.14)."""

    currency: str
    due_soon_count: int
    due_soon_amount: Decimal
    overdue_count: int
    overdue_amount: Decimal
    other_currencies: list[str] = []


class ReceivablesSection(BaseModel):
    """AR outstanding/overdue — from `issued_reports.receivables` (ISSUED_READ +
    issuing module), single-currency."""

    currency: str
    outstanding: Decimal
    overdue: Decimal
    avg_days_to_pay: float | None = None


class CashSection(BaseModel):
    """The working-capital gap (receivables − payables) — verbatim from
    `cash_position.summary` (REPORT_READ). NOT a bank balance; the UI labels it
    honestly (I1.1; the full page relabel is I1.3)."""

    currency: str
    receivables_outstanding: Decimal
    payables_outstanding: Decimal
    net_position: Decimal


class DashboardOut(BaseModel):
    as_of: date
    approvals: ApprovalsSection | None = None
    captures: CapturesSection | None = None
    payables: PayablesSection | None = None
    receivables: ReceivablesSection | None = None
    cash: CashSection | None = None


class OnboardingStep(BaseModel):
    key: str
    label: str
    detail: str
    href: str
    done: bool


class OnboardingOut(BaseModel):
    """WO-P (R19): the derived getting-started card + the caller's authority to
    dismiss it (the SPA must not offer a button the API would 403)."""

    steps: list[OnboardingStep]
    done_count: int
    complete: bool
    dismissed: bool
    can_dismiss: bool
