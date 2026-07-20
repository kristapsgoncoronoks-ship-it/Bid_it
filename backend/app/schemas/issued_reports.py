from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class TimePointOut(BaseModel):
    period: str
    net: Decimal
    gross: Decimal
    count: int


class SummaryReportOut(BaseModel):
    currency: str
    available_currencies: list[str]
    count: int
    net: Decimal
    vat: Decimal
    gross: Decimal
    collected: Decimal
    outstanding: Decimal
    series: list[TimePointOut]


class StatusBucketOut(BaseModel):
    status: str
    label: str
    count: int
    gross: Decimal
    outstanding: Decimal


class AgingBucketOut(BaseModel):
    label: str
    count: int
    outstanding: Decimal


class ReceivablesReportOut(BaseModel):
    currency: str
    available_currencies: list[str]
    statuses: list[StatusBucketOut]
    aging: list[AgingBucketOut]
    total_outstanding: Decimal
    overdue_outstanding: Decimal
    avg_days_to_pay: float | None


class PartnerRowOut(BaseModel):
    partner: str
    vat_number: str | None
    count: int
    net: Decimal
    vat: Decimal
    gross: Decimal
    outstanding: Decimal
    last_invoice: date | None


class PartnerReportOut(BaseModel):
    currency: str
    available_currencies: list[str]
    partners: list[PartnerRowOut]


class VatRateRowOut(BaseModel):
    rate: Decimal
    base: Decimal
    vat: Decimal


class VatSchemeRowOut(BaseModel):
    scheme: str
    net: Decimal
    vat: Decimal


class VatReportOut(BaseModel):
    currency: str
    available_currencies: list[str]
    by_rate: list[VatRateRowOut]
    by_scheme: list[VatSchemeRowOut]
    total_net: Decimal
    total_vat: Decimal
