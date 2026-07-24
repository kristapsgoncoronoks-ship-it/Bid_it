from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class RunCreate(BaseModel):
    invoice_ids: list[str] = Field(min_length=1)
    method: str = "bank_transfer"
    note: str | None = None


class RunPay(BaseModel):
    version: int
    reference: str | None = None
    method: str | None = None


class RunInvoiceOut(BaseModel):
    id: str
    invoice_number: str
    vendor_name: str | None = None
    total: Decimal
    currency: str
    total_eur: Decimal | None = None
    workflow_state: str


class RunOut(BaseModel):
    id: str
    reference: str | None = None
    method: str
    status: str
    note: str | None = None
    total_eur: Decimal
    paid_at: datetime | None = None
    created_by: str | None = None
    version: int
    created_at: datetime
    invoice_count: int


class RunDetailOut(RunOut):
    invoices: list[RunInvoiceOut] = []
