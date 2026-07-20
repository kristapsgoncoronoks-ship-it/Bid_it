from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class SummaryOut(BaseModel):
    total_invoices: int
    total_spend: Decimal
    total_tax: Decimal
    unpaid_amount: Decimal
    avg_invoice: Decimal
    vendor_count: int
    currency: str


class TimeBucket(BaseModel):
    period: str  # YYYY-MM
    total: Decimal
    invoice_count: int


class VendorSpend(BaseModel):
    vendor_id: str
    vendor_name: str
    total: Decimal
    invoice_count: int


class CategorySpend(BaseModel):
    category: str
    total: Decimal


class StatusBucket(BaseModel):
    status: str
    count: int
    total: Decimal


class DimensionSpend(BaseModel):
    """Spend grouped by a cost-allocation dimension value (invoices)."""
    value: str          # the tag value, or "(unassigned)" for untagged spend
    total: Decimal
    invoice_count: int


class DimensionBreakdown(BaseModel):
    dimension: str      # the dimension key (cost_center, vehicle, …)
    label: str          # human label
    rows: list[DimensionSpend]
    total: Decimal
