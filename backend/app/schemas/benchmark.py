from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


# --------------------------------------------------------------------------- #
# Independent — one scorecard per supplier
# --------------------------------------------------------------------------- #
class SupplierBenchmark(BaseModel):
    vendor_id: str
    vendor_name: str
    country: str | None
    invoice_count: int
    total_spend: Decimal
    total_tax: Decimal
    avg_invoice: Decimal
    effective_tax_rate: Decimal  # tax / subtotal, percent
    paid_ratio: Decimal          # paid total / total, 0..1
    category_count: int
    first_invoice: date | None
    last_invoice: date | None
    spend_share: Decimal         # this supplier's share of total spend, percent


# --------------------------------------------------------------------------- #
# Combined — cross-supplier price benchmark per category
# --------------------------------------------------------------------------- #
class SupplierPricePoint(BaseModel):
    vendor_id: str
    vendor_name: str
    unit_price: Decimal          # effective: spend / quantity within the category
    quantity: Decimal
    spend: Decimal
    deviation_pct: Decimal       # vs the combined average unit price, percent
    overspend_vs_cheapest: Decimal  # (unit - cheapest_unit) * qty, floored at 0
    is_cheapest: bool


class CategoryBenchmark(BaseModel):
    category: str
    supplier_count: int
    total_spend: Decimal
    total_quantity: Decimal
    combined_avg_unit: Decimal
    cheapest_vendor_id: str | None
    cheapest_vendor_name: str | None
    cheapest_unit: Decimal
    savings_opportunity: Decimal  # sum of per-supplier overspend vs cheapest
    suppliers: list[SupplierPricePoint]


class BenchmarkSummary(BaseModel):
    supplier_count: int
    total_spend: Decimal
    categories_analyzed: int
    multi_supplier_categories: int
    total_savings_opportunity: Decimal
    currency: str


class CombinedBenchmark(BaseModel):
    summary: BenchmarkSummary
    categories: list[CategoryBenchmark]
