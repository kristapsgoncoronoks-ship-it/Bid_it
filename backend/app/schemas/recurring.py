from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.issued import IssuedInvoiceCreate

Frequency = Literal["weekly", "monthly", "quarterly", "yearly"]


class RecurringCreate(BaseModel):
    """Create a recurring-invoice schedule from an invoice template + cadence."""
    template: IssuedInvoiceCreate
    frequency: Frequency = "monthly"
    interval: int = Field(default=1, ge=1, le=52)     # every N periods
    start_date: date
    end_date: date | None = None                       # inclusive; None → open-ended
    title: str | None = Field(default=None, max_length=200)
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)


class RecurringUpdate(BaseModel):
    """Pause/resume or adjust a schedule. All fields optional."""
    active: bool | None = None
    frequency: Frequency | None = None
    interval: int | None = Field(default=None, ge=1, le=52)
    next_run_date: date | None = None
    end_date: date | None = None
    title: str | None = Field(default=None, max_length=200)
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)


class RecurringOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str | None
    partner_id: str | None
    frequency: str
    interval: int
    start_date: date
    next_run_date: date
    end_date: date | None
    active: bool
    payment_terms_days: int | None
    last_generated_at: date | None
    generated_count: int
    created_at: datetime


class GenerateResult(BaseModel):
    generated: int
    numbers: list[str]
