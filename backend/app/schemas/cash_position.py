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
