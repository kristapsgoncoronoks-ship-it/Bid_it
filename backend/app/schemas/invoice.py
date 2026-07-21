from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.invoice import InvoiceStatus
from app.schemas.dimensions import DimensionFields
from app.schemas.validation import ValidationFinding


class LineItemIn(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    category: str = Field(default="uncategorized", max_length=80)
    quantity: Decimal = Field(default=Decimal("1"), ge=0)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0)
    amount: Decimal | None = Field(default=None, ge=0)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class LineItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    description: str
    category: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    tax_rate: Decimal


class InvoiceCreate(DimensionFields):
    # Either an existing vendor_id or a vendor_name (created/looked up on the fly).
    vendor_id: str | None = None
    vendor_name: str | None = Field(default=None, max_length=200)
    invoice_number: str = Field(min_length=1, max_length=120)
    issue_date: date
    due_date: date | None = None
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    status: InvoiceStatus = InvoiceStatus.pending
    notes: str | None = None
    source_filename: str | None = None
    # FX rate stated on the invoice (foreign-currency units per 1 EUR). Optional;
    # when absent, non-EUR totals are converted at the ECB reference rate.
    fx_rate: Decimal | None = Field(default=None, gt=0)
    line_items: list[LineItemIn] = Field(default_factory=list)


class InvoiceUpdate(DimensionFields):
    status: InvoiceStatus | None = None
    due_date: date | None = None
    notes: str | None = None


class InvoiceOut(DimensionFields):
    model_config = ConfigDict(from_attributes=True)
    id: str
    vendor_id: str
    invoice_number: str
    issue_date: date
    due_date: date | None
    currency: str
    status: InvoiceStatus
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal
    total_eur: Decimal | None = None
    fx_rate: Decimal | None = None
    fx_source: str | None = None
    validation_status: str = "none"
    source_filename: str | None


class InvoiceDetailOut(InvoiceOut):
    vendor_name: str
    notes: str | None
    line_items: list[LineItemOut]
    validation_findings: list[ValidationFinding] = Field(default_factory=list)
    validated_by: str | None = None
    validated_at: datetime | None = None


class InvoiceListOut(BaseModel):
    items: list[InvoiceOut]
    total: int
    page: int
    page_size: int


class ParsedInvoiceDraft(BaseModel):
    """Result of parsing an uploaded file — a *draft* the user confirms."""

    draft: InvoiceCreate
    warnings: list[str] = Field(default_factory=list)
    # How the invoice was read: e-invoice-xml | text-layer | ocr | csv | json.
    method: str = "unknown"
