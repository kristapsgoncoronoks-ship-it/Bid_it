"""Schemas for the invoice review & approval workflow (Phase 08)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Approval policies
# --------------------------------------------------------------------------- #


class ApprovalPolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    active: bool = True
    priority: int = 100
    min_amount: Decimal | None = None
    department_id: str | None = None
    cost_center_id: str | None = None
    legal_entity_id: str | None = None
    vendor_id: str | None = None
    approver_ids: list[str] = Field(default_factory=list)
    finance_final: bool = False
    finance_approver_id: str | None = None


class ApprovalPolicyUpdate(BaseModel):
    version: int
    name: str | None = Field(default=None, max_length=200)
    active: bool | None = None
    priority: int | None = None
    min_amount: Decimal | None = None
    department_id: str | None = None
    cost_center_id: str | None = None
    legal_entity_id: str | None = None
    vendor_id: str | None = None
    approver_ids: list[str] | None = None
    finance_final: bool | None = None
    finance_approver_id: str | None = None


class ApprovalPolicyOut(BaseModel):
    id: str
    name: str
    active: bool
    priority: int
    min_amount: Decimal | None
    department_id: str | None
    cost_center_id: str | None
    legal_entity_id: str | None
    vendor_id: str | None
    approver_ids: list[str]
    finance_final: bool
    finance_approver_id: str | None
    version: int


# --------------------------------------------------------------------------- #
# Review edits
# --------------------------------------------------------------------------- #


class LineIn(BaseModel):
    description: str
    quantity: Decimal = Decimal("1")
    unit_price: Decimal = Decimal("0")
    amount: Decimal | None = None
    tax_rate: Decimal = Decimal("0")
    category: str | None = None


class LinesUpdate(BaseModel):
    version: int
    lines: list[LineIn]


class ReviewHeaderUpdate(BaseModel):
    # Widths MUST match the invoice columns — an over-length value fails only on
    # Postgres (SQLite ignores VARCHAR length), and the create path (InvoiceCreate)
    # is already capped, so this edit path was the one exposed.
    version: int
    vendor_id: str | None = None
    vendor_name: str | None = Field(default=None, max_length=200)
    invoice_number: str | None = Field(default=None, max_length=120)
    issue_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, max_length=3)
    notes: str | None = None
    account_code: str | None = Field(default=None, max_length=80)
    legal_entity_id: str | None = None
    cost_center: str | None = Field(default=None, max_length=80)
    department: str | None = Field(default=None, max_length=80)
    project: str | None = Field(default=None, max_length=80)
    vehicle: str | None = Field(default=None, max_length=80)
    property_ref: str | None = Field(default=None, max_length=80)


# --------------------------------------------------------------------------- #
# Workflow actions
# --------------------------------------------------------------------------- #


class SubmitIn(BaseModel):
    version: int
    note: str | None = None


class DecisionIn(BaseModel):
    version: int
    note: str | None = None


class ReassignIn(BaseModel):
    version: int
    approver_id: str | None = None  # None → open step (any approver)
    step_id: str | None = None  # default: current pending step


class TransitionIn(BaseModel):
    version: int
    target: str  # a WorkflowState value
    note: str | None = None


class CommentIn(BaseModel):
    body: str


# --------------------------------------------------------------------------- #
# Review payload (out)
# --------------------------------------------------------------------------- #


class ReconciliationOut(BaseModel):
    computed_subtotal: Decimal
    computed_tax: Decimal
    computed_total: Decimal
    stated_subtotal: Decimal
    stated_tax: Decimal
    stated_total: Decimal
    balanced: bool
    findings: list[str]


class ApprovalStepOut(BaseModel):
    id: str
    seq: int
    kind: str
    approver_id: str | None
    approver_email: str | None
    status: str
    decided_by_email: str | None
    decided_at: datetime | None
    note: str | None


class InvoiceCommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    author_id: str | None
    author_name: str | None
    body: str
    created_at: datetime


class InvoiceAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str
    mime: str | None
    size: int
    note: str | None
    uploaded_by_email: str | None
    created_at: datetime


class ReviewLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    description: str
    category: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    tax_rate: Decimal


class ReviewOut(BaseModel):
    id: str
    invoice_number: str
    vendor_id: str
    vendor_name: str
    currency: str
    issue_date: date
    due_date: date | None
    status: str  # legacy aging status
    workflow_state: str
    workflow_label: str
    version: int
    locked: bool
    editable: bool
    allowed_targets: list[str]
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal
    total_eur: Decimal | None
    account_code: str | None
    legal_entity_id: str | None
    cost_center: str | None
    department: str | None
    project: str | None
    vehicle: str | None
    property_ref: str | None
    source_filename: str | None
    notes: str | None
    submitted_by: str | None
    submitted_at: datetime | None
    lines: list[ReviewLineOut]
    reconciliation: ReconciliationOut
    approval_steps: list[ApprovalStepOut]
    comments: list[InvoiceCommentOut]
    attachments: list[InvoiceAttachmentOut]


class ApprovalHistoryOut(BaseModel):
    steps: list[ApprovalStepOut]
    events: list[dict]
