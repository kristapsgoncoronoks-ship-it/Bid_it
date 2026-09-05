from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class AgingBucketOut(BaseModel):
    label: str
    count: int
    outstanding: Decimal


class ReceivablesSummary(BaseModel):
    currency: str
    outstanding: Decimal
    overdue: Decimal
    avg_days_to_pay: float | None = None
    aging: list[AgingBucketOut]


class PayablesSummary(BaseModel):
    outstanding: Decimal
    overdue: Decimal
    count: int
    scheduled: int
    in_run: int
    # PERF-002: amounts above are in the report currency; payables held in
    # any other currency are named here rather than silently summed in.
    other_currencies: list[str] = []


class ReconSummary(BaseModel):
    unmatched: int
    matched: int
    ignored: int
    unmatched_amount: Decimal


class CashPositionOut(BaseModel):
    currency: str
    receivables: ReceivablesSummary
    payables: PayablesSummary
    reconciliation: ReconSummary
    net_position: Decimal
