from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class WorklistItemOut(BaseModel):
    id: str
    invoice_number: str
    vendor_name: str | None = None
    due_date: date | None = None
    currency: str
    total: Decimal
    outstanding: Decimal
    status: str
    days_overdue: int
    bucket: str


class ApAgingOut(BaseModel):
    # Amounts are single-currency (`currency`) — see services.ap_aging.DueSummary.
    # Additive wire change (WO-8): existing fields keep their names; `currency`
    # labels them and `other_currencies` surfaces what was NOT folded in.
    currency: str
    due_soon_count: int
    due_soon_amount: Decimal
    overdue_count: int
    overdue_amount: Decimal
    other_currencies: list[str] = []
    items: list[WorklistItemOut]
    # PERF-005/010 (audit 2026-09-05): `items` holds at most `items_limit` rows,
    # soonest-due first; `items_total` is how many open payables there are.
    # The summary figures above cover ALL of them, not only the rows listed.
    items_total: int = 0
    items_limit: int = 0
    truncated: bool = False
