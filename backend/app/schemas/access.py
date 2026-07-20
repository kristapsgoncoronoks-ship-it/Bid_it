from __future__ import annotations

from pydantic import BaseModel, Field


class RolePolicyOut(BaseModel):
    role: str
    label: str
    paid: bool
    description: str
    monthly_invoice_limit: int   # 0 = unlimited
    monthly_upload_limit: int


class RolePolicyUpdate(BaseModel):
    monthly_invoice_limit: int = Field(ge=0)
    monthly_upload_limit: int = Field(ge=0)


class UsageOut(BaseModel):
    role: str
    invoices_used: int
    invoice_limit: int
    invoices_remaining: int | None
    unlimited: bool
