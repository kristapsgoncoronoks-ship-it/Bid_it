from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class RateOut(BaseModel):
    currency: str
    rate: Decimal  # units per 1 EUR
    rate_date: date
    approximate: bool


class RatesResponse(BaseModel):
    base: str = "EUR"
    as_of: date | None
    rates: list[RateOut]


class CurrencyOut(BaseModel):
    code: str
    name: str
    ecb: bool  # published in the ECB reference feed
    rate: Decimal | None  # units per 1 EUR (None if uncached)
    rate_date: date | None
    indicative: bool  # True = not an official ECB rate


class CurrenciesResponse(BaseModel):
    base: str = "EUR"
    region: str = "europe"
    currencies: list[CurrencyOut]


class ConvertResponse(BaseModel):
    amount: Decimal
    from_currency: str
    to_currency: str
    converted: Decimal
    rate: Decimal  # effective from→to rate applied
    rate_date: date
    approximate: bool


class RefreshResult(BaseModel):
    ok: bool
    written: int
    latest_date: str | None = None
    error: str | None = None
    source: str


class FxComparisonRow(BaseModel):
    invoice_id: str
    invoice_number: str
    vendor_name: str
    currency: str
    issue_date: date
    total: Decimal
    ecb_rate: Decimal | None
    ecb_rate_date: date | None
    eur_at_ecb: Decimal | None
    stated_rate: Decimal | None
    eur_at_stated: Decimal | None
    markup_eur: Decimal | None  # eur_at_stated - eur_at_ecb (positive = overpaid vs ECB)
    deviation_pct: Decimal | None  # (stated - ecb) / ecb * 100


class FxComparisonSummary(BaseModel):
    non_eur_invoices: int
    with_stated_rate: int
    total_eur_at_ecb: Decimal
    total_markup_eur: Decimal
    currencies: list[str]


class FxComparison(BaseModel):
    summary: FxComparisonSummary
    rows: list[FxComparisonRow]
