from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class WorklistItemOut(BaseModel):
    id: str
    invoice_number: str
    vendor_name: str | None = None
    due_date: date | None = None
    currency: str
    total: Decimal
    outstanding: Decimal
    status: str
    days_overdue: int
    bucket: str


class ApAgingOut(BaseModel):
    due_soon_count: int
    due_soon_amount: Decimal
    overdue_count: int
    overdue_amount: Decimal
    items: list[WorklistItemOut]
