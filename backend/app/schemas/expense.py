from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.dimensions import DimensionFields

Category = Literal["travel", "meals", "accommodation", "transport", "supplies", "software", "other"]
PaymentMethod = Literal["personal", "company_card"]
ExpenseType = Literal["standard", "mileage", "per_diem"]


class ExpenseItemIn(DimensionFields):
    spend_date: date
    category: Category = "other"
    description: str = Field(min_length=1, max_length=300)
    merchant: str | None = Field(default=None, max_length=200)
    amount: Decimal = Field(default=Decimal("0"), ge=0)
    vat_amount: Decimal = Field(default=Decimal("0"), ge=0)
    reclaimable_tax: bool = True
    payment_method: PaymentMethod = "personal"
    customer_billable: bool = False
    billable_customer: str | None = Field(default=None, max_length=200)
    comment: str | None = Field(default=None, max_length=1000)
    missing_receipt_declaration: str | None = Field(default=None, max_length=500)
    # Original-currency figure + FX provenance (converted amount lands in `amount`).
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    original_amount: Decimal | None = Field(default=None, ge=0)
    fx_rate: Decimal | None = Field(default=None, ge=0)
    fx_source: str | None = Field(default=None, max_length=40)
    # Entry kind + mileage / per-diem inputs. When type is mileage/per_diem and
    # `amount` is 0, the service derives it (distance × rate / days × rate).
    expense_type: ExpenseType = "standard"
    mileage_distance: Decimal | None = Field(default=None, ge=0)
    mileage_rate: Decimal | None = Field(default=None, ge=0)
    mileage_unit: Literal["km", "mi"] | None = None
    per_diem_days: Decimal | None = Field(default=None, ge=0)
    per_diem_rate: Decimal | None = Field(default=None, ge=0)


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
    # Approver / finance actions on a report (the state machine validates legality).
    action: Literal[
        "approve",
        "reject",
        "return_for_correction",
        "mark_for_reimbursement",
        "mark_reimbursed",
        "reimburse",  # legacy alias for mark_reimbursed
    ]
    note: str | None = Field(default=None, max_length=1000)


class ExpenseItemOut(DimensionFields):
    model_config = ConfigDict(from_attributes=True)
    id: str
    spend_date: date
    category: str
    description: str
    merchant: str | None
    amount: Decimal
    currency: str | None = None
    original_amount: Decimal | None = None
    fx_rate: Decimal | None = None
    fx_source: str | None = None
    vat_amount: Decimal
    reclaimable_tax: bool = True
    payment_method: str
    customer_billable: bool = False
    billable_customer: str | None = None
    comment: str | None = None
    missing_receipt_declaration: str | None = None
    expense_type: str = "standard"
    mileage_distance: Decimal | None = None
    mileage_rate: Decimal | None = None
    mileage_unit: str | None = None
    per_diem_days: Decimal | None = None
    per_diem_rate: Decimal | None = None
    has_receipt: bool = False
    verified: bool = False  # reconciled against a bank/card transaction
    bank_reference: str | None = None


class ExpenseItemPatch(DimensionFields):
    """Edit a draft/returned item — business purpose, category, money, flags,
    declarations, and cost dimensions. Only fields explicitly present are applied."""

    description: str | None = Field(default=None, min_length=1, max_length=300)
    merchant: str | None = Field(default=None, max_length=200)
    spend_date: date | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    vat_amount: Decimal | None = Field(default=None, ge=0)
    reclaimable_tax: bool | None = None
    payment_method: PaymentMethod | None = None
    customer_billable: bool | None = None
    billable_customer: str | None = Field(default=None, max_length=200)
    comment: str | None = Field(default=None, max_length=1000)
    missing_receipt_declaration: str | None = Field(default=None, max_length=500)
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
    code: str
    message: str
    severity: Literal["warn", "block"] = "warn"
    item_id: str | None = None
    category: str | None = None
    amount: Decimal | None = None
    limit: Decimal | None = None


class ExpensePolicyIn(BaseModel):
    active: bool = True
    max_item_amount: Decimal | None = None
    receipt_required_over: Decimal | None = None
    category_caps: dict[str, Decimal] = Field(default_factory=dict)
    allowed_categories: list[str] = Field(default_factory=list)
    allowed_currencies: list[str] = Field(default_factory=list)
    warn_weekend: bool = False
    duplicate_detection: bool = True
    mileage_rate: Decimal | None = None
    mileage_rate_tolerance: Decimal | None = None
    require_purpose_over: Decimal | None = None
    late_submission_days: int | None = Field(default=None, ge=0)
    # Rule codes that HARD-BLOCK submission; anything else only warns.
    blocking_rules: list[str] = Field(default_factory=list)


class ExpensePolicyOut(BaseModel):
    active: bool
    max_item_amount: Decimal | None
    receipt_required_over: Decimal | None
    category_caps: dict[str, Decimal]
    allowed_categories: list[str] = Field(default_factory=list)
    allowed_currencies: list[str] = Field(default_factory=list)
    warn_weekend: bool = False
    duplicate_detection: bool = True
    mileage_rate: Decimal | None = None
    mileage_rate_tolerance: Decimal | None = None
    require_purpose_over: Decimal | None = None
    late_submission_days: int | None = None
    blocking_rules: list[str] = Field(default_factory=list)
    version: int


class PolicyCheckOut(BaseModel):
    """A dry-run policy evaluation: every finding + whether submission is blocked."""

    violations: list[PolicyViolation] = []
    blocking: bool = False
    can_submit: bool = True


class ApprovalStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    seq: int
    kind: str
    approver_id: str | None = None
    approver_email: str | None = None
    status: str
    decided_by_email: str | None = None
    decided_at: datetime | None = None
    note: str | None = None


class ExpenseReportDetail(ExpenseReportOut):
    note: str | None
    decided_at: datetime | None
    decided_by: str | None
    decision_note: str | None
    reimbursed_at: datetime | None = None
    payment_reference: str | None = None
    items: list[ExpenseItemOut]
    policy_violations: list[PolicyViolation] = []
    approval_steps: list[ApprovalStepOut] = []


class ReassignIn(BaseModel):
    approver_id: str
    step_id: str | None = None  # default: the current pending step


class ExpenseApprovalPolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    active: bool = True
    priority: int = 100
    min_amount: Decimal | None = None
    approver_ids: list[str] = Field(default_factory=list)
    finance_final: bool = False
    finance_approver_id: str | None = None


class ExpenseApprovalPolicyUpdate(ExpenseApprovalPolicyIn):
    version: int


class ExpenseApprovalPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    active: bool
    priority: int
    min_amount: Decimal | None = None
    approver_ids: list[str] = []
    finance_final: bool = False
    finance_approver_id: str | None = None
    version: int


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
