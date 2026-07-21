from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

DocKind = Literal["contract", "acceptance_act"]


class PartnerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    vat_number: str | None = Field(default=None, max_length=32)
    address_line1: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    requires_contract: bool = False
    requires_acceptance: bool = False
    penalty_enabled: bool = False
    penalty_rate: Decimal | None = Field(default=None, ge=0, le=100)
    is_active: bool = True


class PartnerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    vat_number: str | None = Field(default=None, max_length=32)
    address_line1: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    requires_contract: bool | None = None
    requires_acceptance: bool | None = None
    penalty_enabled: bool | None = None
    penalty_rate: Decimal | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None


class PartnerDocumentIn(BaseModel):
    kind: DocKind
    title: str = Field(min_length=1, max_length=200)
    reference: str | None = Field(default=None, max_length=64)
    note: str | None = None


class DocumentSignIn(BaseModel):
    signed_by: str | None = Field(default=None, max_length=200)
    signed_date: date | None = None


class PartnerDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    title: str
    reference: str | None
    status: str
    signed_by: str | None
    signed_date: date | None
    note: str | None


class ReadinessOut(BaseModel):
    ready: bool
    required: list[str]
    signed: list[str]
    missing: list[str]


class PartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    email: str | None
    vat_number: str | None
    address_line1: str | None
    city: str | None
    postal_code: str | None
    country: str | None
    requires_contract: bool
    requires_acceptance: bool
    penalty_enabled: bool
    penalty_rate: Decimal | None
    is_active: bool


class PartnerDetail(PartnerOut):
    documents: list[PartnerDocumentOut] = []
    readiness: ReadinessOut
    has_signed_contract: bool = False


class PenaltyLineOut(BaseModel):
    invoice_id: str
    number: str
    days_overdue: int
    outstanding: Decimal
    penalty: Decimal


class PenaltySummaryOut(BaseModel):
    currency: str
    total_penalty: Decimal
    total_outstanding: Decimal
    max_days_overdue: int
    lines: list[PenaltyLineOut]
    can_generate: bool  # penalty enabled + contract signed + penalty > 0
    blocked_reason: str | None = None
