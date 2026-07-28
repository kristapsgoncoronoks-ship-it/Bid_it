from __future__ import annotations

from pydantic import BaseModel, Field


class PlanPolicyOut(BaseModel):
    plan: str
    label: str
    paid: bool
    description: str
    monthly_invoice_limit: int  # 0 = unlimited
    monthly_upload_limit: int


class PlanPolicyUpdate(BaseModel):
    monthly_invoice_limit: int = Field(ge=0)
    monthly_upload_limit: int = Field(ge=0)


class UsageOut(BaseModel):
    plan: str
    invoices_used: int
    invoice_limit: int
    invoices_remaining: int | None
    unlimited: bool
