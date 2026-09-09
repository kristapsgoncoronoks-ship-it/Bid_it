"""The composed home dashboard — an Insight-layer PROJECTION (ADR-0023, WO-16).

One read answering "what needs me today": approvals waiting on the caller,
captures pending review, payables due soon / overdue, receivables overdue, and
the working-capital gap. Every figure comes VERBATIM from the canonical service
that owns it (`approval_policy`, `expense_approval`, `payment_run`, `vendors`,
`extraction`, `ap_aging`, `issued_reports`, `cash_position`) — this module adds
NO arithmetic on amounts (its only sums are integer counts of composed items),
owns no tables and mutates nothing.

Permission model: each section is gated by the SAME permission its canonical
route declares (and, where that route module-gates, by the same module), so the
composed endpoint can never widen what a role could already read — a section the
caller may not see is ``None``, never zeroed (the SPA hides the card). A section
that ERRORS is a bug and propagates: all sources are same-transaction reads of
the same DB, so partial-failure masking would only hide defects (fail loud, not
fail soft).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import authz
from app.core.authz import Permission as P
from app.schemas.dashboard import (
    ApApprovalItem,
    ApprovalsSection,
    CapturesSection,
    CashSection,
    DashboardOut,
    PayablesSection,
    ReceivablesSection,
)
from app.services import (
    ap_aging,
    approval_policy,
    cash_position,
    expense_approval,
    extraction,
    issued_reports,
    modules,
    payment_run,
)
from app.services import vendors as vendor_service


async def _approvals(
    db: AsyncSession, user, org_id: str, enabled: set[str]
) -> ApprovalsSection | None:
    can_ap = authz.has(user, P.INVOICE_APPROVE)
    can_exp = authz.has(user, P.EXPENSE_APPROVE) and "expenses" in enabled
    can_run = authz.has(user, P.PAYMENT_WRITE)
    can_vcr = authz.has(user, P.SETTINGS_MANAGE)
    if not (can_ap or can_exp or can_run or can_vcr):
        return None

    invoices: list[ApApprovalItem] | None = None
    invoice_count: int | None = None
    if can_ap:
        items = await approval_policy.waiting_for(
            db, org_id, user_id=user.id, can_approve_any=True, limit=10
        )
        invoices = [
            ApApprovalItem(
                invoice_id=it.invoice_id,
                invoice_number=it.invoice_number,
                vendor_name=it.vendor_name,
                total=it.total,
                currency=it.currency,
            )
            for it in items
        ]
        invoice_count = len(items)

    expense_reports = await expense_approval.pending_report_count(db, org_id) if can_exp else None
    payment_runs = (
        await payment_run.runs_awaiting_check(db, org_id, checker_id=user.id) if can_run else None
    )
    vendor_changes = (
        await vendor_service.pending_change_count(db, org_id, exclude_requester_id=user.id)
        if can_vcr
        else None
    )
    total = sum(
        n for n in (invoice_count, expense_reports, payment_runs, vendor_changes) if n is not None
    )
    return ApprovalsSection(
        invoices=invoices,
        invoice_count=invoice_count,
        expense_reports=expense_reports,
        payment_runs=payment_runs,
        vendor_changes=vendor_changes,
        total=total,
    )


async def home(db: AsyncSession, user, org_id: str, today: date | None = None) -> DashboardOut:
    """The full composed dashboard for one caller. Read-only; see module doc."""
    today = today or date.today()
    enabled = await modules.enabled_keys(db, org_id)

    approvals = await _approvals(db, user, org_id, enabled)

    captures: CapturesSection | None = None
    if authz.has(user, P.INVOICE_READ):
        rq = await extraction.review_queue_summary(db, org_id)
        captures = CapturesSection(
            pending=rq.pending, low_confidence_fields=rq.low_confidence_fields
        )

    wants_cash = authz.has(user, P.REPORT_READ)
    wants_receivables = authz.has(user, P.ISSUED_READ) and "issuing" in enabled
    # PERF-003 aggregated this read in SQL; PERF-018 stopped running it twice.
    # The cash card's net position and the receivables card are two views of ONE
    # canonical read, so it happens once per request and only when a card that
    # shows it survived the permission gates. Without the bands: neither card
    # renders them (`with_aging=False` → `rep.aging is None`).
    rep = (
        await issued_reports.receivables_scalars(db, org_id, today=today, with_aging=False)
        if (wants_cash or wants_receivables)
        else None
    )

    payables: PayablesSection | None = None
    cash: CashSection | None = None
    if wants_cash:
        due = await ap_aging.due_summary(db, org_id, today)  # PERF-002: aggregated in SQL
        payables = PayablesSection(
            currency=due.currency,
            due_soon_count=due.due_soon_count,
            due_soon_amount=due.due_soon_amount,
            overdue_count=due.overdue_count,
            overdue_amount=due.overdue_amount,
            other_currencies=list(due.other_currencies),
        )
        pos = await cash_position.net_figures(db, org_id, today, receivables=rep)
        cash = CashSection(
            currency=pos["currency"],
            receivables_outstanding=pos["receivables_outstanding"],
            payables_outstanding=pos["payables_outstanding"],
            net_position=pos["net_position"],
        )

    receivables: ReceivablesSection | None = None
    if wants_receivables:
        assert rep is not None  # wants_receivables was one of the two reasons it was read
        receivables = ReceivablesSection(
            currency=rep.currency,
            outstanding=rep.total_outstanding,
            overdue=rep.overdue_outstanding,
            avg_days_to_pay=rep.avg_days_to_pay,
        )

    return DashboardOut(
        as_of=today,
        approvals=approvals,
        captures=captures,
        payables=payables,
        receivables=receivables,
        cash=cash,
    )
