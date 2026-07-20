from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VatScheme = Literal["standard", "reverse_charge", "intra_eu", "exempt"]


class IssuedLineIn(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit: str = Field(default="C62", max_length=8)
    unit_price: Decimal = Field(ge=0)
    vat_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class IssuedInvoiceCreate(BaseModel):
    buyer_name: str = Field(min_length=1, max_length=200)
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
    issue_date: date
    supply_date: date | None
    due_date: date | None
    currency: str
    buyer_name: str
    buyer_vat_number: str | None
    vat_scheme: str
    note: str | None
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    amount_paid: Decimal = Decimal("0")
    paid_date: date | None = None
    status: str = "open"            # derived: paid | partial | open | overdue
    outstanding: Decimal = Decimal("0")


class PaymentUpdate(BaseModel):
    """Record a payment against an issued invoice (accounts-receivable)."""
    amount_paid: Decimal = Field(ge=0)
    paid_date: date | None = None


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
