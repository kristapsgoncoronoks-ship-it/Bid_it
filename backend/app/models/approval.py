"""Approval routing — policies and per-invoice approval steps (Phase 08).

An **ApprovalPolicy** is a tenant-scoped rule: *when an invoice matches these
criteria, route it through this ordered chain of approvers*. The criteria are the
initial practical subset — amount threshold, department, cost center, legal
entity, supplier — any subset may be set (an unset criterion is a wildcard). The
first active policy (lowest `priority`) whose criteria ALL match wins.

When an invoice is submitted, the matching policy is expanded into an ordered set
of **ApprovalStep** rows (one per approver, plus an optional finance-final step).
Approvals advance the chain step by step; a rejection ends it. The steps are the
immutable-ish record of *who was asked, who decided, and how* — the approval
history the UI shows and the audit trail references.

Both are tenant tables (org_id + RLS + ORM guard). Cross-tenant links are made
structurally impossible via composite FKs to same-org parents.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

# Per-step decision states.
STEP_PENDING = "pending"
STEP_APPROVED = "approved"
STEP_REJECTED = "rejected"
STEP_SKIPPED = "skipped"  # chain ended before this step was reached
STEP_STATUSES = (STEP_PENDING, STEP_APPROVED, STEP_REJECTED, STEP_SKIPPED)

# A step whose approver is "any user holding INVOICE_APPROVE" (no named approver).
KIND_APPROVER = "approver"
KIND_FINANCE_FINAL = "finance_final"


class ApprovalPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "approval_policies"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_approval_policies_org_id"),
        Index("ix_approval_policies_org_active", "org_id", "active", "priority"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Lower runs first; the first fully-matching active policy wins.
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    # --- Match criteria (all nullable = wildcard) ---------------------------
    # Amount threshold: policy applies when the invoice EUR total >= this.
    min_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    department_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    cost_center_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    legal_entity_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    vendor_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)

    # --- Approval chain ------------------------------------------------------
    # Ordered approver user-ids as a JSON array (sequential). Empty ⇒ a single
    # generic step approvable by any INVOICE_APPROVE holder.
    approver_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Append a finance final-approval step after the chain.
    finance_final: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Specific finance approver for that step (else any INVOICE_APPROVE holder).
    finance_approver_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class ApprovalStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "approval_steps"
    __table_args__ = (
        # Cross-tenant-safe link: the invoice MUST belong to the same org.
        ForeignKeyConstraint(
            ["org_id", "invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_approval_steps_invoice",
            ondelete="CASCADE",
        ),
        UniqueConstraint("org_id", "invoice_id", "seq", name="uq_approval_steps_invoice_seq"),
        Index("ix_approval_steps_invoice", "org_id", "invoice_id"),
        Index("ix_approval_steps_approver", "org_id", "approver_id", "status"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    policy_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    # 0-based order within this invoice's chain.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default=KIND_APPROVER, nullable=False)
    # Assigned approver; NULL ⇒ any user with INVOICE_APPROVE may act on it.
    approver_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    approver_email: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[str] = mapped_column(String(16), default=STEP_PENDING, nullable=False)
    decided_by: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    decided_by_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
