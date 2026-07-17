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
