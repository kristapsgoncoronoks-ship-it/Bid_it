from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SupplierPaymentRecord(BaseModel):
    # The new CUMULATIVE amount paid on the invoice; the ledger records the change.
    amount_paid: Decimal = Field(ge=0)
    paid_date: date | None = None
    method: str = Field(default="bank_transfer", max_length=20)
    reference: str | None = Field(default=None, max_length=140)


class SupplierPaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    invoice_id: str
    amount: Decimal  # signed: payment out +, correction −
    paid_on: date
    method: str
    reference: str | None = None
    note: str | None = None
    created_at: datetime
