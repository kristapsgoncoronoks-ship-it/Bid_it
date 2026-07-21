from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ReceiptCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    received_on: date
    method: str = Field(default="bank_transfer", max_length=20)
    reference: str | None = Field(default=None, max_length=140)
    note: str | None = None


class AllocateRequest(BaseModel):
    invoice_id: str
    amount: Decimal = Field(gt=0)


class ReceiptAllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    issued_invoice_id: str
    amount: Decimal
    paid_on: date
    reference: str | None = None


class ReceiptOut(BaseModel):
    id: str
    amount: Decimal
    received_on: date
    method: str
    reference: str | None = None
    note: str | None = None
    allocated: Decimal
    unallocated: Decimal
    created_at: datetime


class ReceiptDetail(ReceiptOut):
    allocations: list[ReceiptAllocationOut] = []
