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
    vat_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class IssuedInvoiceCreate(BaseModel):
    # Optional link to a Partner — when set, its pre-invoicing workflow is enforced.
    partner_id: str | None = None
    buyer_name: str = Field(min_length=1, max_length=200)
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
    # Late-payment interest (% p.a.); omit to inherit the issuer default (if any).
    penalty_rate: Decimal | None = Field(default=None, ge=0, le=100)
    lines: list[IssuedLineIn] = Field(min_length=1)


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
    vat_rate: Decimal
    net_amount: Decimal


class IssuedInvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    number: str
    kind: str = "standard"          # standard | penalty
    partner_id: str | None = None
    issue_date: date
    supply_date: date | None
    due_date: date | None
    currency: str
    buyer_name: str
    buyer_vat_number: str | None
    vat_scheme: str
    note: str | None
    buyer_email: str | None = None
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    amount_paid: Decimal = Decimal("0")
    paid_date: date | None = None
    status: str = "open"            # derived: paid | partial | open | overdue
    outstanding: Decimal = Decimal("0")
    penalty_rate: Decimal | None = None
    penalty_accrued: Decimal = Decimal("0")   # advisory late interest
    days_overdue: int = 0
    reminder_count: int = 0
    last_reminder_at: date | None = None


class PaymentUpdate(BaseModel):
    """Record a payment against an issued invoice (accounts-receivable)."""
    amount_paid: Decimal = Field(ge=0)
    paid_date: date | None = None


class SendRequest(BaseModel):
    """Email an issued invoice (PDF attached). Recipient defaults to buyer_email."""
    to_email: EmailStr | None = None


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
    delivered: bool          # True when relayed via SMTP; False when only recorded


class BulkReminderResult(BaseModel):
    sent: int
    skipped_no_email: int
    messages: list[EmailMessageOut]


class IssuedInvoiceDetail(IssuedInvoiceOut):
    buyer_address_line1: str | None
    buyer_city: str | None
    buyer_postal_code: str | None
    buyer_country: str | None
    lines: list[IssuedLineOut]
    vat_breakdown: list[VatBucketOut] = []


class IssuedInvoiceListOut(BaseModel):
    items: list[IssuedInvoiceOut]
    total: int
