"""Read-only reports over a tenant's ISSUED invoices (accounts-receivable).

Every report is tenant-scoped, takes an optional `[start, end]` issue-date
window, and operates on a SINGLE currency (mixing currencies in one total is
meaningless) — the currency is either passed in or inferred as the tenant's
most-used one. Amounts are quantized once, in EUR-style cents, via `money`.

Issued-invoice volume per tenant is modest, so the row set is fetched once and
aggregated in Python — this keeps the `today`-relative status/aging logic in a
single place (`issued_status`) instead of duplicating it in portable SQL.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import money
from app.models.issued_invoice import IssuedInvoice, IssuedInvoiceLine
from app.services import issued_status as st

_ZERO = Decimal("0")

# Aging buckets for outstanding balances, in days past due (upper-bound, label).
_AGING = [(0, "Current"), (30, "1–30"), (60, "31–60"), (90, "61–90")]
_AGING_OVER = "90+"


def _sum(values) -> Decimal:
    total = _ZERO
    for v in values:
        total += Decimal(v or _ZERO)
    return money.q2(total)


def _signed(inv: IssuedInvoice, value) -> Decimal:
    """A credit note SUBTRACTS from turnover; an invoice adds."""
    v = Decimal(value or _ZERO)
    return -v if st.is_credit_note(inv) else v


async def _pick_currency(db: AsyncSession, org_id: str, currency: str | None) -> tuple[str, list[str]]:
    """Resolve the report currency and list the currencies present for the tenant."""
    rows = list(await db.execute(
        select(IssuedInvoice.currency, func.count(IssuedInvoice.id))
        .where(IssuedInvoice.org_id == org_id)
        .group_by(IssuedInvoice.currency)
        .order_by(func.count(IssuedInvoice.id).desc())
    ))
    available = sorted(c for c, _ in rows)
    if currency:
        return currency.upper(), available
    return (rows[0][0] if rows else "EUR"), available


async def _rows(db: AsyncSession, org_id: str, currency: str,
                start: date | None, end: date | None) -> list[IssuedInvoice]:
    stmt = select(IssuedInvoice).where(
        IssuedInvoice.org_id == org_id, IssuedInvoice.currency == currency
    )
    if start is not None:
        stmt = stmt.where(IssuedInvoice.issue_date >= start)
    if end is not None:
        stmt = stmt.where(IssuedInvoice.issue_date <= end)
    return list(await db.scalars(stmt.order_by(IssuedInvoice.issue_date)))


# --- 1. Summary + turnover trend ------------------------------------------------

@dataclass
class TimePoint:
    period: str          # YYYY-MM
    net: Decimal
    gross: Decimal
    count: int


@dataclass
class SummaryReport:
    currency: str
    available_currencies: list[str]
    count: int
    net: Decimal         # subtotal (VAT-exclusive)
    vat: Decimal
    gross: Decimal       # total (VAT-inclusive)
    collected: Decimal
    outstanding: Decimal
    series: list[TimePoint] = field(default_factory=list)


async def summary(db, org_id, currency, start, end) -> SummaryReport:
    cur, available = await _pick_currency(db, org_id, currency)
    rows = await _rows(db, org_id, cur, start, end)

    buckets: dict[str, dict] = defaultdict(lambda: {"net": _ZERO, "gross": _ZERO, "count": 0})
    for inv in rows:
        b = buckets[inv.issue_date.strftime("%Y-%m")]
        # Turnover is NET of credit notes; count is invoices only (credit notes
        # are corrections, not sales).
        b["net"] += _signed(inv, inv.subtotal)
        b["gross"] += _signed(inv, inv.total)
        if not st.is_credit_note(inv):
            b["count"] += 1

    series = [
        TimePoint(period=p, net=money.q2(b["net"]), gross=money.q2(b["gross"]), count=b["count"])
        for p, b in sorted(buckets.items())
    ]
    invoices_only = [inv for inv in rows if not st.is_credit_note(inv)]
    return SummaryReport(
        currency=cur,
        available_currencies=available,
        count=len(invoices_only),
        net=_sum(_signed(inv, inv.subtotal) for inv in rows),
        vat=_sum(_signed(inv, inv.tax_total) for inv in rows),
        gross=_sum(_signed(inv, inv.total) for inv in rows),
        collected=_sum(inv.amount_paid for inv in rows),
        outstanding=_sum(st.outstanding_of(inv) for inv in rows),
        series=series,
    )


# --- 2. Receivables: paid / unpaid / overdue + aging + DSO ----------------------

@dataclass
class StatusBucket:
    status: str
    label: str
    count: int
    gross: Decimal
    outstanding: Decimal


@dataclass
class AgingBucket:
    label: str
    count: int
    outstanding: Decimal


@dataclass
class ReceivablesReport:
    currency: str
    available_currencies: list[str]
    statuses: list[StatusBucket]
    aging: list[AgingBucket]
    total_outstanding: Decimal
    overdue_outstanding: Decimal
    penalty_accrued: Decimal        # advisory late-payment interest on overdue
    avg_days_to_pay: float | None   # DSO proxy over settled invoices


async def receivables(db, org_id, currency, start, end, today: date | None = None) -> ReceivablesReport:
    today = today or date.today()
    cur, available = await _pick_currency(db, org_id, currency)
    rows = await _rows(db, org_id, cur, start, end)

    order = [st.PAID, st.PARTIAL, st.OPEN, st.OVERDUE, st.CREDITED]
    sb: dict[str, dict] = {s: {"count": 0, "gross": _ZERO, "outstanding": _ZERO} for s in order}
    aging: dict[str, dict] = {lbl: {"count": 0, "outstanding": _ZERO} for _, lbl in _AGING}
    aging[_AGING_OVER] = {"count": 0, "outstanding": _ZERO}

    days_to_pay: list[int] = []
    for inv in rows:
        # Credit notes are corrections, not receivables — they never appear in the
        # paid/overdue/aging buckets. Their effect is already netted into each
        # corrected invoice's outstanding balance via `credited_total`.
        if st.is_credit_note(inv):
            continue
        status = st.status_of(inv, today)
        out = st.outstanding_of(inv)
        s = sb[status]
        s["count"] += 1
        s["gross"] += Decimal(inv.total)
        s["outstanding"] += out

        # Aging is over amounts STILL OWED (open/partial/overdue), by days past due.
        if out > _ZERO and inv.due_date is not None:
            past = (today - inv.due_date).days
            label = _AGING_OVER
            for upper, lbl in _AGING:
                if past <= upper:
                    label = lbl
                    break
            aging[label]["count"] += 1
            aging[label]["outstanding"] += out

        # DSO proxy: issue → paid, over invoices that are settled with a paid_date.
        if status == st.PAID and inv.paid_date is not None:
            days_to_pay.append((inv.paid_date - inv.issue_date).days)

    statuses = [
        StatusBucket(status=s, label=st.STATUS_LABELS[s], count=sb[s]["count"],
                     gross=money.q2(sb[s]["gross"]), outstanding=money.q2(sb[s]["outstanding"]))
        for s in order
    ]
    aging_out = [
        AgingBucket(label=lbl, count=aging[lbl]["count"], outstanding=money.q2(aging[lbl]["outstanding"]))
        for _, lbl in _AGING
    ] + [AgingBucket(label=_AGING_OVER, count=aging[_AGING_OVER]["count"],
                     outstanding=money.q2(aging[_AGING_OVER]["outstanding"]))]

    total_out = _sum(st.outstanding_of(inv) for inv in rows)
    overdue_out = money.q2(sb[st.OVERDUE]["outstanding"])
    penalty = _sum(st.penalty_of(inv, today) for inv in rows)
    avg = round(sum(days_to_pay) / len(days_to_pay), 1) if days_to_pay else None
    return ReceivablesReport(cur, available, statuses, aging_out, total_out, overdue_out, penalty, avg)


# --- 3. Partners / turnover by partner ------------------------------------------

@dataclass
class PartnerRow:
    partner: str
    vat_number: str | None
    count: int
    net: Decimal
    vat: Decimal
    gross: Decimal
    outstanding: Decimal
    last_invoice: date | None


@dataclass
class PartnerReport:
    currency: str
    available_currencies: list[str]
    partners: list[PartnerRow]


async def by_partner(db, org_id, currency, start, end) -> PartnerReport:
    cur, available = await _pick_currency(db, org_id, currency)
    rows = await _rows(db, org_id, cur, start, end)

    # Group by (name, vat) so two customers sharing a name but not a VAT id stay apart.
    agg: dict[tuple, dict] = defaultdict(
        lambda: {"count": 0, "net": _ZERO, "vat": _ZERO, "gross": _ZERO,
                 "outstanding": _ZERO, "last": None})
    for inv in rows:
        key = (inv.buyer_name, inv.buyer_vat_number)
        a = agg[key]
        if not st.is_credit_note(inv):
            a["count"] += 1
        a["net"] += _signed(inv, inv.subtotal)
        a["vat"] += _signed(inv, inv.tax_total)
        a["gross"] += _signed(inv, inv.total)
        a["outstanding"] += st.outstanding_of(inv)
        if a["last"] is None or inv.issue_date > a["last"]:
            a["last"] = inv.issue_date

    partners = [
        PartnerRow(partner=name, vat_number=vat, count=a["count"],
                   net=money.q2(a["net"]), vat=money.q2(a["vat"]), gross=money.q2(a["gross"]),
                   outstanding=money.q2(a["outstanding"]), last_invoice=a["last"])
        for (name, vat), a in agg.items()
    ]
    partners.sort(key=lambda p: p.gross, reverse=True)
    return PartnerReport(cur, available, partners)


# --- 4. Output-VAT summary (by rate + by scheme) --------------------------------

@dataclass
class VatRateRow:
    rate: Decimal
    base: Decimal
    vat: Decimal


@dataclass
class VatSchemeRow:
    scheme: str
    net: Decimal
    vat: Decimal


@dataclass
class VatReport:
    currency: str
    available_currencies: list[str]
    by_rate: list[VatRateRow]
    by_scheme: list[VatSchemeRow]
    total_net: Decimal
    total_vat: Decimal


async def vat_summary(db, org_id, currency, start, end) -> VatReport:
    cur, available = await _pick_currency(db, org_id, currency)

    # Line-level net grouped by (scheme, rate). VAT only accrues on the standard
    # scheme; reverse-charge / intra-EU / exempt carry a rate on the line but 0 tax.
    stmt = (
        select(IssuedInvoice.vat_scheme, IssuedInvoice.doc_type, IssuedInvoiceLine.vat_rate,
               func.coalesce(func.sum(IssuedInvoiceLine.net_amount), 0))
        .join(IssuedInvoice, IssuedInvoiceLine.invoice_id == IssuedInvoice.id)
        .where(IssuedInvoice.org_id == org_id, IssuedInvoice.currency == cur)
        .group_by(IssuedInvoice.vat_scheme, IssuedInvoice.doc_type, IssuedInvoiceLine.vat_rate)
    )
    if start is not None:
        stmt = stmt.where(IssuedInvoice.issue_date >= start)
    if end is not None:
        stmt = stmt.where(IssuedInvoice.issue_date <= end)

    by_rate: dict[Decimal, dict] = defaultdict(lambda: {"base": _ZERO, "vat": _ZERO})
    by_scheme: dict[str, dict] = defaultdict(lambda: {"net": _ZERO, "vat": _ZERO})
    for scheme, doc_type, rate, net in await db.execute(stmt):
        net = Decimal(net or _ZERO)
        # A credit note reduces the output-VAT base and the VAT owed.
        if doc_type == "credit_note":
            net = -net
        vat = money.q2(net * Decimal(rate) / Decimal(100)) if scheme == "standard" else _ZERO
        by_rate[Decimal(rate)]["base"] += net
        by_rate[Decimal(rate)]["vat"] += vat
        by_scheme[scheme]["net"] += net
        by_scheme[scheme]["vat"] += vat

    rate_rows = [
        VatRateRow(rate=r, base=money.q2(v["base"]), vat=money.q2(v["vat"]))
        for r, v in sorted(by_rate.items())
    ]
    scheme_rows = [
        VatSchemeRow(scheme=s, net=money.q2(v["net"]), vat=money.q2(v["vat"]))
        for s, v in sorted(by_scheme.items())
    ]
    return VatReport(
        cur, available, rate_rows, scheme_rows,
        total_net=_sum(r.base for r in rate_rows),
        total_vat=_sum(r.vat for r in rate_rows),
    )
