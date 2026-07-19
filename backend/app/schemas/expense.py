from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["travel", "meals", "accommodation", "transport", "supplies", "software", "other"]
PaymentMethod = Literal["personal", "company_card"]


class ExpenseItemIn(BaseModel):
    spend_date: date
    category: Category = "other"
    description: str = Field(min_length=1, max_length=300)
    merchant: str | None = Field(default=None, max_length=200)
    amount: Decimal = Field(ge=0)
    vat_amount: Decimal = Field(default=Decimal("0"), ge=0)
    payment_method: PaymentMethod = "personal"


class ExpenseReportCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    note: str | None = Field(default=None, max_length=1000)
    items: list[ExpenseItemIn] = Field(default_factory=list)


class ExpenseReportUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    note: str | None = None
    items: list[ExpenseItemIn] | None = None


class ExpenseDecision(BaseModel):
    action: Literal["approve", "reject", "reimburse"]
    note: str | None = Field(default=None, max_length=1000)


class ExpenseItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    spend_date: date
    category: str
    description: str
    merchant: str | None
    amount: Decimal
    vat_amount: Decimal
    payment_method: str
    has_receipt: bool = False


class ExpenseReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    employee_id: str
    employee_name: str
    title: str
    status: str
    currency: str
    total: Decimal
    vat_total: Decimal
    total_eur: Decimal | None
    submitted_at: datetime | None
    created_at: datetime


class ExpenseReportDetail(ExpenseReportOut):
    note: str | None
    decided_at: datetime | None
    decided_by: str | None
    decision_note: str | None
    items: list[ExpenseItemOut]


class ExpenseReportListOut(BaseModel):
    items: list[ExpenseReportOut]
    total: int


class CategoryTotal(BaseModel):
    category: str
    total: Decimal


class ExpenseSummary(BaseModel):
    my_draft: int
    my_submitted: int
    my_reimbursable: Decimal      # approved but not yet reimbursed (mine)
    reclaimable_vat: Decimal      # my total reclaimable VAT
    pending_approvals: int        # awaiting my decision (approver only)
    by_category: list[CategoryTotal]
    currency: str = "EUR"
