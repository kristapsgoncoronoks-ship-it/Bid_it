from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

VatScheme = Literal["standard", "reverse_charge", "intra_eu", "exempt"]


class IssuedLineIn(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit: str = Field(default="C62", max_length=8)
    unit_price: Decimal = Field(ge=0)
    # Per-line discount as a % of qty×unit_price (net is stored post-discount).
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    vat_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    # Optional tax-code catalogue reference; when set, its rate overrides vat_rate.
    tax_code: str | None = Field(default=None, max_length=24)


class IssuedInvoiceCreate(BaseModel):
    # The issuer legal entity to invoice AS (its own numbering series + seller
    # snapshot). Omit to use the org's default issuer.
    issuer_id: str | None = None
    # Optional link to a Partner — when set, its pre-invoicing workflow is enforced.
    partner_id: str | None = None
    # The project (won contract/job) this revenue belongs to — the revenue side
    # of the project P&L (docs/design/project-profitability.md).
    project_id: str | None = None
    # Optional link to a sales Customer — its billing details prefill the buyer
    # block (and payment terms / currency) when not given explicitly.
    customer_id: str | None = None
    # Required unless a customer_id supplies it (validated server-side).
    buyer_name: str | None = Field(default=None, max_length=200)
    buyer_email: EmailStr | None = None
    buyer_vat_number: str | None = Field(default=None, max_length=32)
    buyer_address_line1: str | None = Field(default=None, max_length=200)
    buyer_city: str | None = Field(default=None, max_length=120)
    buyer_postal_code: str | None = Field(default=None, max_length=20)
    buyer_country: str | None = Field(default=None, min_length=2, max_length=2)

    issue_date: date | None = None
    supply_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    vat_scheme: VatScheme = "standard"
    note: str | None = Field(default=None, max_length=1000)
    # Buyer's purchase-order reference (EN-16931 BT-13).
    po_reference: str | None = Field(default=None, max_length=60)
    # VAT-exemption reason (EN-16931 BT-120); defaults from the scheme note when a
    # zero-VAT scheme is chosen and this is omitted.
    tax_exemption_reason: str | None = Field(default=None, max_length=300)
    # Late-payment interest (% p.a.); omit to inherit the issuer default (if any).
    penalty_rate: Decimal | None = Field(default=None, ge=0, le=100)
    lines: list[IssuedLineIn] = Field(min_length=1)
    # Create as an editable DRAFT (no number, no partner signed-gate) instead of a
    # born-final invoice. A draft is numbered and finalized later via /issue.
    draft: bool = False


class VatBucketOut(BaseModel):
    rate: Decimal
    base: Decimal
    vat: Decimal


class IssuedLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    position: int
    description: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    discount_percent: Decimal = Decimal("0")
    vat_rate: Decimal
    net_amount: Decimal
    tax_code: str | None = None


class IssuedInvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    number: str | None = None  # NULL until a draft is issued
    lifecycle: str = "issued"  # draft | approved | issued | disputed | written_off | cancelled
    issuer_id: str | None = None
    kind: str = "standard"  # standard | penalty
    doc_type: str = "invoice"  # invoice | credit_note
    corrected_invoice_id: str | None = None
    # WO-K: Art. 219 reference snapshot (credit notes only) — on the LIST shape
    # so the register can say which invoice a credit note corrects.
    corrected_invoice_number: str | None = None
    credited_total: Decimal = Decimal("0")  # sum of credit notes applied to this invoice
    partner_id: str | None = None
    issue_date: date
    supply_date: date | None
    due_date: date | None
    currency: str
    buyer_name: str
    buyer_vat_number: str | None
    vat_scheme: str
    note: str | None
    po_reference: str | None = None
    tax_exemption_reason: str | None = None
    buyer_email: str | None = None
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    amount_paid: Decimal = Decimal("0")
    paid_date: date | None = None
    status: str = "open"  # derived: paid | partial | open | overdue
    outstanding: Decimal = Decimal("0")
    penalty_rate: Decimal | None = None
    penalty_accrued: Decimal = Decimal("0")  # advisory late interest
    days_overdue: int = 0
    reminder_count: int = 0
    last_reminder_at: date | None = None
    sent_at: datetime | None = None
    viewed_at: datetime | None = None
    voided_at: datetime | None = None
    void_reason: str | None = None
    disputed_at: datetime | None = None
    dispute_reason: str | None = None
    written_off_at: datetime | None = None
    writeoff_reason: str | None = None
    approved_at: datetime | None = None
    issued_at: datetime | None = None


class VoidRequest(BaseModel):
    reason: str | None = None


class IssueRequest(BaseModel):
    """Finalize a draft/approved invoice: allocate its number and set it live.
    `issue_date` re-stamps the issue date (default = today); the due date is
    recomputed keeping the draft's payment-term gap."""

    issue_date: date | None = None


class DisputeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class WriteOffRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class PaymentUpdate(BaseModel):
    """Record a payment against an issued invoice (accounts-receivable). The value
    is the new CUMULATIVE amount paid; the ledger records the change."""

    amount_paid: Decimal = Field(ge=0)
    paid_date: date | None = None


class PaymentOut(BaseModel):
    """One entry in an invoice's payment ledger (history)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    amount: Decimal  # signed: + receipt, - correction/refund
    paid_on: date
    method: str
    reference: str | None = None
    note: str | None = None
    created_at: datetime


class CreditNoteCreate(BaseModel):
    """Issue a credit note against an existing invoice.

    Omit `lines` to credit the WHOLE remaining (un-credited) invoice; pass lines to
    credit specific amounts (a partial credit). The credited total may not exceed
    the invoice's still-un-credited amount.
    """

    lines: list[IssuedLineIn] | None = None
    issue_date: date | None = None
    reason: str | None = Field(default=None, max_length=1000)


class SendRequest(BaseModel):
    """Email an issued invoice (PDF attached). Recipient defaults to buyer_email."""

    to_email: EmailStr | None = None
    # Send is IDEMPOTENT: once delivered, a repeat call is a no-op that returns the
    # first send. Set resend=true to deliberately dispatch the invoice again.
    resend: bool = False


class ReminderRequest(BaseModel):
    """Send a payment reminder for an overdue invoice."""

    to_email: EmailStr | None = None


class EmailMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    invoice_id: str | None
    kind: str
    to_email: str
    subject: str
    status: str
    error: str | None
    created_at: datetime


class SendResult(BaseModel):
    message: EmailMessageOut
    delivered: bool  # True when relayed via SMTP; False when only recorded
    already_sent: bool = False  # True when this was an idempotent no-op re-request


class BulkReminderResult(BaseModel):
    sent: int
    skipped_no_email: int
    skipped: int = 0  # overdue but no new ladder level due
    messages: list[EmailMessageOut]


class IssuedInvoiceDetail(IssuedInvoiceOut):
    buyer_address_line1: str | None
    buyer_city: str | None
    buyer_postal_code: str | None
    buyer_country: str | None
    lines: list[IssuedLineOut]
    vat_breakdown: list[VatBucketOut] = []
    # WO-K: the ADVISORY late-payment computation (overdue invoices only; see
    # services/late_interest). Detail-only — it needs the org's configured rate.
    late_interest: dict | None = None


class IssuedInvoiceListOut(BaseModel):
    items: list[IssuedInvoiceOut]
    total: int


class IssuedAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str
    mime: str | None = None
    size: int
    note: str | None = None
    uploaded_by_email: str | None = None
    created_at: datetime
