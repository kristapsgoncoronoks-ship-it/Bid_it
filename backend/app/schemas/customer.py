from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CustomerContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    role: str | None = Field(default=None, max_length=80)


class CustomerContactOut(CustomerContactIn):
    model_config = ConfigDict(from_attributes=True)
    id: str


class CustomerBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=200)
    vat_number: str | None = Field(default=None, max_length=32)
    registration_number: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=2)
    ship_address_line1: str | None = Field(default=None, max_length=200)
    ship_address_line2: str | None = Field(default=None, max_length=200)
    ship_city: str | None = Field(default=None, max_length=120)
    ship_postal_code: str | None = Field(default=None, max_length=20)
    ship_country: str | None = Field(default=None, max_length=2)
    payment_terms_days: int | None = Field(default=None, ge=0)
    default_currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = None
    is_active: bool = True


class CustomerCreate(CustomerBase):
    contacts: list[CustomerContactIn] = Field(default_factory=list)


class CustomerUpdate(BaseModel):
    # Every field optional; contacts (when present) replace the whole list. Widths
    # MUST match the column caps (and CustomerBase) — a value over the limit fails
    # only on Postgres (SQLite ignores VARCHAR length), so an uncapped field here
    # is a portability bug the SQLite test suite can't catch.
    name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=200)
    vat_number: str | None = Field(default=None, max_length=32)
    registration_number: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=2)
    ship_address_line1: str | None = Field(default=None, max_length=200)
    ship_address_line2: str | None = Field(default=None, max_length=200)
    ship_city: str | None = Field(default=None, max_length=120)
    ship_postal_code: str | None = Field(default=None, max_length=20)
    ship_country: str | None = Field(default=None, max_length=2)
    payment_terms_days: int | None = Field(default=None, ge=0)
    default_currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = None
    is_active: bool | None = None
    contacts: list[CustomerContactIn] | None = None


class CustomerOut(CustomerBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime
    lifecycle: str = "active"
    contacts: list[CustomerContactOut] = []
