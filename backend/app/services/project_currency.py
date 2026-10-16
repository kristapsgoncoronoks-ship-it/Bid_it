"""Project EUR ledger projection using the existing WO-8 FX convention.

One batched source projection for detail/list/freeze. No source or frozen money is
rewritten. Rate evidence follows each source into the caller's close-time snapshot.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import money
from app.models.expense import ExpenseItem, ExpenseReport
from app.models.invoice import Invoice, LineItem
from app.models.invoice_project_split import InvoiceProjectSplit
from app.models.issued_invoice import IssuedInvoice
from app.models.project_link import ProjectCostEntry
from app.services import fx

ZERO = Decimal("0")


async def project_ledgers(db: AsyncSession, org_id: str, *, project_id: str | None = None) -> dict:
    """Fixed statement count, scoped to the tenant and optionally one project."""

    def selected(column):
        return column == project_id if project_id is not None else column.is_not(None)

    issued = list(
        await db.execute(
            select(
                IssuedInvoice.id,
                IssuedInvoice.project_id,
                IssuedInvoice.currency,
                IssuedInvoice.issue_date,
                IssuedInvoice.subtotal,
                IssuedInvoice.doc_type,
                IssuedInvoice.number,
            )
            .where(
                IssuedInvoice.org_id == org_id,
                selected(IssuedInvoice.project_id),
                IssuedInvoice.lifecycle.not_in(("draft", "cancelled")),
            )
            .order_by(IssuedInvoice.id)
        )
    )
    # Include every tag on a relevant invoice when computing its remainder.
    tagged_ids = select(LineItem.invoice_id).where(selected(LineItem.project_id))
    split_ids = select(InvoiceProjectSplit.invoice_id).where(
        InvoiceProjectSplit.org_id == org_id, selected(InvoiceProjectSplit.project_id)
    )
    relevant = select(Invoice.id).where(
        Invoice.org_id == org_id,
        selected(Invoice.project_id) | Invoice.id.in_(tagged_ids) | Invoice.id.in_(split_ids),
    )
    invoices = list(
        await db.execute(
            select(
                Invoice.id,
                Invoice.project_id,
                Invoice.currency,
                Invoice.issue_date,
                Invoice.subtotal,
                Invoice.total,
                Invoice.fx_rate,
                Invoice.invoice_number,
            )
            .where(Invoice.org_id == org_id, Invoice.id.in_(relevant))
            .order_by(Invoice.id)
        )
    )
    lines = list(
        await db.execute(
            select(LineItem.invoice_id, LineItem.project_id, LineItem.amount)
            .join(Invoice, Invoice.id == LineItem.invoice_id)
            .where(
                Invoice.org_id == org_id, Invoice.id.in_(relevant), LineItem.project_id.is_not(None)
            )
            .order_by(LineItem.id)
        )
    )
    splits = list(
        await db.scalars(
            select(InvoiceProjectSplit)
            .where(
                InvoiceProjectSplit.org_id == org_id, InvoiceProjectSplit.invoice_id.in_(relevant)
            )
            .order_by(InvoiceProjectSplit.project_id)
        )
    )
    expenses = list(
        await db.execute(
            select(
                ExpenseItem.id,
                ExpenseItem.project_id,
                ExpenseItem.amount,
                ExpenseItem.vat_amount,
                ExpenseItem.spend_date,
                ExpenseReport.currency,
                ExpenseItem.currency.label("receipt_currency"),
                ExpenseItem.original_amount,
                ExpenseItem.fx_source,
                ExpenseItem.fx_rate,
            )
            .join(ExpenseReport, ExpenseReport.id == ExpenseItem.report_id)
            .where(
                ExpenseReport.org_id == org_id,
                ExpenseItem.org_id == org_id,
                selected(ExpenseItem.project_id),
                ExpenseReport.status.in_(("approved", "processing", "reimbursed")),
            )
            .order_by(ExpenseItem.id)
        )
    )
    manual = list(
        await db.execute(
            select(
                ProjectCostEntry.id,
                ProjectCostEntry.project_id,
                ProjectCostEntry.amount,
                ProjectCostEntry.currency,
                ProjectCostEntry.entry_date,
            )
            .where(ProjectCostEntry.org_id == org_id, selected(ProjectCostEntry.project_id))
            .order_by(ProjectCostEntry.id)
        )
    )
    codes = (
        {item.currency for item in issued if item.currency}
        | {item.currency for item in invoices if item.currency}
        | {item.currency for item in expenses if item.currency}
        | {item.currency for item in manual if item.currency}
    )
    series = await fx.load_rate_series(db, codes)
    ledgers: dict = {}

    def ledger(pid):
        return ledgers.setdefault(
            pid,
            {
                "invoiced": ZERO,
                "credited": ZERO,
                "invoice_costs": ZERO,
                "expense_costs": ZERO,
                "manual_costs": ZERO,
                "fx_trace": [],
                "issues": [],
            },
        )

    def convert(source_id, kind, amount, currency, on_date, stated_rate=None, source_basis="net"):
        currency = (currency or "").upper()
        rate_date = None
        approximate = False
        rate = None
        source = "unknown"
        if currency == "EUR":
            rate, source = Decimal("1"), "eur"
        elif (
            stated_rate is not None
            and Decimal(stated_rate).is_finite()
            and Decimal(stated_rate) > 0
        ):
            rate, source = Decimal(stated_rate), "stated"
        elif on_date is not None:
            resolved = fx.resolve_from(series, currency, on_date)
            if resolved:
                rate, source = resolved.rate, "ecb"
                rate_date, approximate = resolved.rate_date, resolved.approximate
        value = (
            money.q2(Decimal(amount) / rate)
            if rate is not None and rate.is_finite() and rate > 0 and Decimal(amount).is_finite()
            else None
        )
        trace = {
            "source_id": source_id,
            "kind": kind,
            "currency": currency,
            "original_amount": str(money.q2(Decimal(amount))),
            "amount_eur": str(value) if value is not None else None,
            "on_date": on_date.isoformat() if on_date else None,
            "rate": str(rate) if rate else None,
            "rate_date": rate_date.isoformat() if rate_date else None,
            "fx_source": source,
            "approximate": approximate,
            "source_basis": source_basis,
        }
        return value, trace

    def record(pid, bucket, value, trace):
        if project_id is not None and pid != project_id:
            return
        row = ledger(pid)
        row["fx_trace"].append(trace)
        if value is None:
            row["issues"].append(
                {
                    "code": trace.get("issue_code", "missing_fx"),
                    "source_id": trace["source_id"],
                    "kind": trace["kind"],
                    "currency": trace["currency"],
                    "detail": trace.get(
                        "issue_detail",
                        "No supported EUR conversion is available; review the source/rate evidence.",
                    ),
                }
            )
        else:
            row[bucket] += value

    for issued_row in issued:
        value, trace = convert(
            issued_row.id, "issued", issued_row.subtotal, issued_row.currency, issued_row.issue_date
        )
        trace["document_number"] = issued_row.number
        record(
            issued_row.project_id,
            "credited" if issued_row.doc_type == "credit_note" else "invoiced",
            value,
            trace,
        )
    by_invoice: dict = {}
    for line_row in lines:
        by_invoice.setdefault(line_row.invoice_id, []).append(line_row)
    split_map: dict = {}
    for split_row in splits:
        split_map.setdefault(split_row.invoice_id, []).append(split_row)
    for inv in invoices:
        amount = inv.subtotal if inv.subtotal is not None else inv.total or ZERO
        base, trace = convert(
            inv.id,
            "received",
            amount,
            inv.currency,
            inv.issue_date,
            inv.fx_rate,
            "net" if inv.subtotal is not None else "gross_fallback",
        )
        trace["document_number"] = inv.invoice_number
        tagged = by_invoice.get(inv.id, [])
        shares = split_map.get(inv.id, [])
        destinations = {line.project_id for line in tagged} | {share.project_id for share in shares}
        if not shares and inv.project_id:
            destinations.add(inv.project_id)
        if base is None:
            for pid in sorted(destinations):
                record(pid, "invoice_costs", None, dict(trace, allocated_eur=None))
            continue
        tagged_total = ZERO
        allocated: dict = {}
        for line in tagged:
            tagged_total += line.amount
            allocated[line.project_id] = allocated.get(line.project_id, ZERO) + line.amount
        if tagged_total > amount:
            for pid in sorted(destinations):
                invalid = dict(
                    trace,
                    issue_code="allocation_exceeds_source",
                    issue_detail="Allocated lines exceed the document base; review the source and allocations.",
                )
                record(pid, "invoice_costs", None, invalid)
            continue
        remainder = money.q2(amount - tagged_total)
        if shares:
            from app.services.project_profit import _split_shares

            for pid, value in _split_shares(remainder, shares).items():
                allocated[pid] = allocated.get(pid, ZERO) + value
        else:
            # Include the unallocated part in rounding, without booking it to a project.
            allocated[inv.project_id] = allocated.get(inv.project_id, ZERO) + remainder
        eur_shares = {
            pid: money.q2(base * local / amount) if amount else ZERO
            for pid, local in allocated.items()
        }
        residue = money.q2(base - sum(eur_shares.values(), ZERO))
        if residue and allocated:
            largest = max(allocated, key=lambda pid: (allocated[pid], str(pid or "")))
            eur_shares[largest] = money.q2(eur_shares[largest] + residue)
        for pid, value in sorted(eur_shares.items(), key=lambda item: str(item[0] or "")):
            if pid is not None:
                record(
                    pid,
                    "invoice_costs",
                    value,
                    dict(
                        trace,
                        allocated_eur=str(value),
                        allocated_original=str(money.q2(allocated[pid])),
                    ),
                )
    for expense_row in expenses:
        value, trace = convert(
            expense_row.id,
            "expense_report_item",
            expense_row.amount - expense_row.vat_amount,
            expense_row.currency,
            expense_row.spend_date,
        )
        if (
            expense_row.original_amount is not None
            and expense_row.receipt_currency
            and expense_row.receipt_currency.upper() != expense_row.currency.upper()
        ):
            trace.update(
                receipt_currency=expense_row.receipt_currency,
                receipt_original_amount=str(expense_row.original_amount),
                receipt_fx_source=expense_row.fx_source,
                receipt_fx_rate=str(expense_row.fx_rate)
                if expense_row.fx_rate is not None
                else None,
            )
            if (
                expense_row.fx_source not in ("stated", "ecb")
                or expense_row.fx_rate is None
                or not expense_row.fx_rate.is_finite()
                or expense_row.fx_rate <= 0
            ):
                value = None
                trace.update(
                    amount_eur=None,
                    issue_code="unverified_receipt_conversion",
                    issue_detail="The receipt-to-report conversion has no verified rate evidence; review the original expense.",
                )
        record(expense_row.project_id, "expense_costs", value, trace)
    for manual_row in manual:
        value, trace = convert(
            manual_row.id, "manual", manual_row.amount, manual_row.currency, manual_row.entry_date
        )
        record(manual_row.project_id, "manual_costs", value, trace)
    return ledgers
