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
    # Slice 5b: the capture run this invoice was saved from (from the upload
    # draft); linked to the invoice on save. Absent for a manually-entered invoice.
    extraction_run_id: str | None = None
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


class FieldProvenance(BaseModel):
    """Per-field capture provenance (Slice 5f): how a top-level invoice field was
    obtained. `status`: extracted (read from the source) | defaulted (filled in) |
    missing (absent, no default). `confidence` is reserved for OCR/AI paths."""

    field: str
    value: str | None = None
    status: str = "extracted"
    confidence: Decimal | None = None


class ParsedInvoiceDraft(BaseModel):
    """Result of parsing an uploaded file — a *draft* the user confirms."""

    draft: InvoiceCreate
    warnings: list[str] = Field(default_factory=list)
    # How the invoice was read: e-invoice-xml | text-layer | ocr | csv | json.
    method: str = "unknown"
    # Slice 5b: the recorded capture run; echo it back on save to link the lineage.
    extraction_run_id: str | None = None
    # Slice 5f: per-field provenance (populated by the deterministic parsers).
    fields: list[FieldProvenance] = Field(default_factory=list)


class UploadAccepted(BaseModel):
    """202 response to a direct upload — the parse/OCR is queued on the worker
    tier (Stage B). Poll GET /invoices/upload/{extraction_run_id} for the draft."""

    extraction_run_id: str
    status: str = "queued"  # queued | running


class ExtractionResult(BaseModel):
    """Poll response for an async upload capture. `draft` is populated once
    `status == "parsed"`; `error` carries the reason when `status == "failed"`."""

    extraction_run_id: str
    status: str  # queued | running | parsed | failed
    method: str | None = None
    draft: ParsedInvoiceDraft | None = None
    error: str | None = None


class FieldProvenanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    field: str
    value: str | None = None
    status: str
    confidence: Decimal | None = None


class ExtractionRunOut(BaseModel):
    """A capture-lineage entry — how a received invoice was read."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    method: str
    status: str
    source_filename: str | None = None
    source_sha256: str | None = None
    field_count: int
    warning_count: int
    note: str | None = None
    created_at: datetime
    fields: list[FieldProvenanceOut] = []
