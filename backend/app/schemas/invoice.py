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
    # AP review-&-approval lifecycle state (additive on the LIST shape, WO-16 —
    # the approvals worklist filter renders it; the detail shape already carried
    # it since Phase 13, same `str | None` type).
    workflow_state: str | None = None
    source_filename: str | None


class InvoiceDetailOut(InvoiceOut):
    vendor_name: str
    notes: str | None
    line_items: list[LineItemOut]
    validation_findings: list[ValidationFinding] = Field(default_factory=list)
    validated_by: str | None = None
    validated_at: datetime | None = None
    # AP settlement (Phase 13). `payment_status` is derived (paid/partial/open/overdue).
    # (`workflow_state` moved to the InvoiceOut base — WO-16, same type/default.)
    amount_paid: Decimal = Decimal("0")
    paid_date: date | None = None
    outstanding: Decimal = Decimal("0")
    payment_status: str = "open"


class InvoiceListOut(BaseModel):
    items: list[InvoiceOut]
    total: int
    page: int
    page_size: int


class FieldProvenance(BaseModel):
    """Per-field capture provenance: how an invoice field was obtained.

    Two scopes (E1.2): `line_index` None = one of the five header fields;
    `line_index = n` = one field of `line_items[n]` (description / category /
    quantity / unit_price / amount / tax_rate) — the same honest semantics
    apply to both.

    `status`: extracted (read from the source) | defaulted (filled in) | missing
    (absent, no default). Extraction is NOT assumed accurate — every field carries:
      • `confidence` (0..1) — set by probabilistic providers (OCR/AI); None for
        deterministic structured parsers (there is no score to report), so a None
        confidence means "exact", not "unknown".
      • `original_value` / `normalized_value` — the raw captured text vs. the cleaned
        value used downstream (equal for deterministic parsers today; the seam lets
        a provider report both).
      • `reviewed_value` — a human-corrected value, set when someone reviews the
        capture; None until then.
      • `provider` — which extraction provider produced the field.
      • `low_confidence` — a flag the review queue keys on (below threshold, or a
        non-extracted status)."""

    field: str
    value: str | None = None  # effective (normalized) value — kept for back-compat
    status: str = "extracted"
    confidence: Decimal | None = None
    original_value: str | None = None
    normalized_value: str | None = None
    reviewed_value: str | None = None
    provider: str | None = None
    low_confidence: bool = False
    line_index: int | None = None  # None = header field; n = line_items[n] (E1.2)


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


class DuplicateCandidateOut(BaseModel):
    invoice_id: str
    vendor_id: str
    vendor_name: str
    invoice_number: str
    issue_date: str | None = None
    total: str
    currency: str
    status: str


class DuplicateReportOut(BaseModel):
    """Same-number invoices split by supplier — `exact` (same supplier, likely a true
    duplicate) vs `cross_supplier` (a different supplier, usually a coincidence)."""

    invoice_number: str
    exact: list[DuplicateCandidateOut] = Field(default_factory=list)
    cross_supplier: list[DuplicateCandidateOut] = Field(default_factory=list)


class CaptureReviewItem(BaseModel):
    """One parsed-but-unconfirmed capture in the human-review queue."""

    extraction_run_id: str
    method: str
    status: str
    source_filename: str | None = None
    invoice_number: str | None = None
    vendor_name: str | None = None
    warning_count: int
    total_fields: int
    low_confidence_fields: int
    duplicate_candidate: bool = False
    created_at: datetime


class CaptureReviewQueueOut(BaseModel):
    items: list[CaptureReviewItem]
    total: int


class FieldReviewIn(BaseModel):
    field: str = Field(min_length=1, max_length=40)
    reviewed_value: str = Field(max_length=500)
    # None targets the header row for `field`; n targets line_items[n]'s row (E1.2).
    line_index: int | None = Field(default=None, ge=0)


class CaptureReviewIn(BaseModel):
    """Human corrections for a capture's fields — records the reviewed value per
    field and clears its low-confidence flag."""

    fields: list[FieldReviewIn] = Field(default_factory=list)


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
    original_value: str | None = None
    normalized_value: str | None = None
    reviewed_value: str | None = None
    provider: str | None = None
    low_confidence: bool = False
    line_index: int | None = None  # None = header field; n = line_items[n] (E1.2)


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
