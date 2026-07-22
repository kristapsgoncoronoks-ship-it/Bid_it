from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.dimensions import DimensionFields

Category = Literal["travel", "meals", "accommodation", "transport", "supplies", "software", "other"]
PaymentMethod = Literal["personal", "company_card"]


class ExpenseItemIn(DimensionFields):
    spend_date: date
    category: Category = "other"
    description: str = Field(min_length=1, max_length=300)
    merchant: str | None = Field(default=None, max_length=200)
    amount: Decimal = Field(ge=0)
    vat_amount: Decimal = Field(default=Decimal("0"), ge=0)
    payment_method: PaymentMethod = "personal"
    comment: str | None = Field(default=None, max_length=1000)


class ExpenseReportCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    note: str | None = Field(default=None, max_length=1000)
    items: list[ExpenseItemIn] = Field(default_factory=list)
    # Concur-style: build the report from selected inbox transactions.
    transaction_ids: list[str] = Field(default_factory=list)


class ExpenseReportUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    note: str | None = None
    items: list[ExpenseItemIn] | None = None


class ExpenseDecision(BaseModel):
    action: Literal["approve", "reject", "reimburse"]
    note: str | None = Field(default=None, max_length=1000)


class ExpenseItemOut(DimensionFields):
    model_config = ConfigDict(from_attributes=True)
    id: str
    spend_date: date
    category: str
    description: str
    merchant: str | None
    amount: Decimal
    vat_amount: Decimal
    payment_method: str
    comment: str | None = None
    has_receipt: bool = False
    verified: bool = False  # reconciled against a bank/card transaction
    bank_reference: str | None = None


class ExpenseItemPatch(DimensionFields):
    """Edit a draft item's business purpose, category, and cost dimensions."""

    comment: str | None = Field(default=None, max_length=1000)
    category: Category | None = None


class MatchTransaction(BaseModel):
    """Reconcile a draft expense item against a bank/card statement transaction."""

    transaction_id: str


class ItemFromTransaction(BaseModel):
    transaction_id: str
    category: Category = "other"
    vat_amount: Decimal = Field(default=Decimal("0"), ge=0)


class ExpenseCommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class ExpenseCommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    author_name: str
    body: str
    created_at: datetime


class ExpenseTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    txn_date: date
    description: str
    merchant: str | None
    amount: Decimal
    currency: str
    direction: str
    source: str
    status: str


class BankImportResult(BaseModel):
    method: str
    imported: int
    transactions: list[ExpenseTransactionOut]
    warnings: list[str] = []


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


class PolicyViolation(BaseModel):
    item_id: str
    category: str
    code: str
    message: str
    amount: Decimal
    limit: Decimal


class ExpensePolicyIn(BaseModel):
    active: bool = True
    max_item_amount: Decimal | None = None
    receipt_required_over: Decimal | None = None
    category_caps: dict[str, Decimal] = Field(default_factory=dict)


class ExpensePolicyOut(BaseModel):
    active: bool
    max_item_amount: Decimal | None
    receipt_required_over: Decimal | None
    category_caps: dict[str, Decimal]
    version: int


class ExpenseReportDetail(ExpenseReportOut):
    note: str | None
    decided_at: datetime | None
    decided_by: str | None
    decision_note: str | None
    reimbursed_at: datetime | None = None
    payment_reference: str | None = None
    items: list[ExpenseItemOut]
    policy_violations: list[PolicyViolation] = []


class ExpenseReportListOut(BaseModel):
    items: list[ExpenseReportOut]
    total: int


class CategoryTotal(BaseModel):
    category: str
    total: Decimal


class BankTransaction(BaseModel):
    date: date
    description: str
    amount: Decimal
    direction: Literal["debit", "credit"]
    balance: Decimal | None = None


class BankStatementDraft(BaseModel):
    method: str  # text-layer | ocr | csv
    transactions: list[BankTransaction]
    suggested_items: list[ExpenseItemIn]  # debits → draft expense items
    warnings: list[str] = []


class ExpenseSummary(BaseModel):
    my_draft: int
    my_submitted: int
    my_reimbursable: Decimal  # approved but not yet reimbursed (mine)
    reclaimable_vat: Decimal  # my total reclaimable VAT
    pending_approvals: int  # awaiting my decision (approver only)
    by_category: list[CategoryTotal]
    currency: str = "EUR"
