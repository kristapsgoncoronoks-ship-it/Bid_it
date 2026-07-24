"""Cash-flow trend (Phase 19).

A monthly inflow / outflow / net series from the settlement ledgers — the
time-dimension complement to the point-in-time cash-position dashboard. Inflow =
the AR payment ledger (receipts, direct issued payments, net of corrections);
outflow = supplier payments + paid expense reimbursement payouts. Bucketed in
Python (portable across SQLite/Postgres, like the receivables report). NET EUR;
read-only, tenant-scoped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import money
from app.models.expense import ReimbursementBatch
from app.models.payment import Payment
from app.models.supplier_payment import SupplierPayment

_ZERO = Decimal("0")


@dataclass
class CashFlowPoint:
    period: str  # YYYY-MM
    inflow: Decimal
    outflow: Decimal
    net: Decimal


def _month_window(today: date, months: int) -> list[tuple[int, int]]:
    """The (year, month) pairs for the last `months` months, oldest first."""
    y, m = today.year, today.month
    out: list[tuple[int, int]] = []
    for _ in range(months):
        out.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


async def monthly(
    db: AsyncSession, org_id: str, months: int = 12, today: date | None = None
) -> list[CashFlowPoint]:
    today = today or date.today()
    window = _month_window(today, months)
    start = date(window[0][0], window[0][1], 1)
    keys: dict[tuple[int, int], dict[str, Decimal]] = {
        ym: {"inflow": _ZERO, "outflow": _ZERO} for ym in window
    }

    def _add(d: date | None, field: str, amount: Decimal) -> None:
        if d is None:
            return
        k = (d.year, d.month)
        if k in keys:
            keys[k][field] += Decimal(amount)

    # AR inflow — the signed payment ledger (receipts + direct + corrections).
    for p in await db.scalars(
        select(Payment).where(Payment.org_id == org_id, Payment.paid_on >= start)
    ):
        _add(p.paid_on, "inflow", p.amount)
    # AP outflow — supplier payments.
    for sp in await db.scalars(
        select(SupplierPayment).where(
            SupplierPayment.org_id == org_id, SupplierPayment.paid_on >= start
        )
    ):
        _add(sp.paid_on, "outflow", sp.amount)
    # AP outflow — paid expense reimbursement payouts.
    for b in await db.scalars(
        select(ReimbursementBatch).where(
            ReimbursementBatch.org_id == org_id,
            ReimbursementBatch.status == "paid",
            ReimbursementBatch.paid_at.is_not(None),
        )
    ):
        _add(b.paid_at.date() if b.paid_at else None, "outflow", b.total_eur)

    series: list[CashFlowPoint] = []
    for y, m in window:
        inflow = money.q2(keys[(y, m)]["inflow"])
        outflow = money.q2(keys[(y, m)]["outflow"])
        series.append(
            CashFlowPoint(
                period=f"{y:04d}-{m:02d}",
                inflow=inflow,
                outflow=outflow,
                net=money.q2(inflow - outflow),
            )
        )
    return series
