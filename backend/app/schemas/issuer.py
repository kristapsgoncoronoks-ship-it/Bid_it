from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class IssuerProfileIn(BaseModel):
    legal_name: str | None = Field(default=None, max_length=200)
    trade_name: str | None = Field(default=None, max_length=200)
    vat_number: str | None = Field(default=None, max_length=32)
    registration_number: str | None = Field(default=None, max_length=64)
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    iban: str | None = Field(default=None, max_length=34)
    bic: str | None = Field(default=None, max_length=11)
    default_currency: str | None = Field(default=None, min_length=3, max_length=3)
    invoice_prefix: str | None = Field(default=None, max_length=16)
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    notes: str | None = None


class IssuerProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    legal_name: str | None
    trade_name: str | None
    vat_number: str | None
    registration_number: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    postal_code: str | None
    country: str | None
    email: str | None
    phone: str | None
    iban: str | None
    bic: str | None
    default_currency: str
    invoice_prefix: str
    next_number: int
    payment_terms_days: int
    notes: str | None
    is_complete: bool = False
    missing_fields: list[str] = []
    has_logo: bool = False
