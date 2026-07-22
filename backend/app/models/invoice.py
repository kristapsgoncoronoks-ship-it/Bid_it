from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
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
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.vendor import Vendor


class InvoiceStatus(str, enum.Enum):
    draft = "draft"
    pending = "pending"
    paid = "paid"
    overdue = "overdue"


class WorkflowState(str, enum.Enum):
    """The AP review-&-approval lifecycle (see services/invoice_workflow.py, the
    transition state machine). Runs alongside the legacy aging `status` above,
    which analytics/payments still read; the two are synced at the paid milestone."""

    uploaded = "uploaded"
    processing = "processing"
    review_required = "review_required"
    draft = "draft"
    submitted = "submitted"  # "Submitted for Approval"
    partially_approved = "partially_approved"
    approved = "approved"
    rejected = "rejected"
    scheduled_for_payment = "scheduled_for_payment"
    partially_paid = "partially_paid"
    paid = "paid"
    disputed = "disputed"
    cancelled = "cancelled"
    archived = "archived"


# 14 digits total, 2 after the decimal — exact money, never float.
Money = Numeric(14, 2)


class Invoice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        # Analytics fact scan (tenant + time window).
        Index("ix_invoices_org_issue", "org_id", "issue_date"),
        # Foreign-currency scans (fx.ecb_comparison, explore currency filter).
        Index("ix_invoices_org_currency", "org_id", "currency"),
        # Composite-FK target for child tables that link tenant-safely (extraction
        # lineage; future line-level refs).
        UniqueConstraint("org_id", "id", name="uq_invoices_org_id"),
        # Slice 2: normalised links to cost-allocation master data. Composite FK
        # (org_id, *_id) → master(org_id, id) makes a cross-tenant link
        # structurally impossible (same guard as cost_centers → departments).
        # ON DELETE SET NULL is nominal only — master rows are archived, never
        # hard-deleted, so it never actually fires (see services/costing).
        ForeignKeyConstraint(
            ["org_id", "cost_center_id"],
            ["cost_centers.org_id", "cost_centers.id"],
            name="fk_invoices_cost_center",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["org_id", "department_id"],
            ["departments.org_id", "departments.id"],
            name="fk_invoices_department",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_invoices_project",
            ondelete="SET NULL",
        ),
        # Rollup scans: "every invoice allocated to master row X".
        Index("ix_invoices_org_cost_center", "org_id", "cost_center_id"),
        Index("ix_invoices_org_department", "org_id", "department_id"),
        Index("ix_invoices_org_project", "org_id", "project_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )

    invoice_number: Mapped[str] = mapped_column(String(120), nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        SAEnum(InvoiceStatus, name="invoice_status"),
        default=InvoiceStatus.pending,
        nullable=False,
        index=True,
    )

    subtotal: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    total: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)

    # FX: rate is foreign-currency units per 1 EUR (ECB convention). `fx_rate` is
    # the rate stated on the invoice (if any); `total_eur` is the EUR-converted
    # total; `fx_source` records how it was converted (eur/stated/ecb/unknown).
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    total_eur: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    fx_source: Mapped[str | None] = mapped_column(String(16), nullable=True)

    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- AP review & approval lifecycle (Phase 08) ---------------------------
    # The canonical processing state (see services/invoice_workflow.py). Legacy
    # `status` above stays for aging/analytics and is synced at the paid milestone.
    workflow_state: Mapped[WorkflowState] = mapped_column(
        SAEnum(WorkflowState, name="invoice_workflow_state"),
        default=WorkflowState.draft,
        server_default=WorkflowState.draft.value,
        nullable=False,
        index=True,
    )
    # Optimistic concurrency: bumped on every mutating operation. A reviewer sends
    # the version they read; a stale write is rejected 409 (invoice_workflow.assert_version).
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    # Record lock: set when the invoice reaches `approved`; cleared by a controlled
    # correction (reopen → draft). A locked record refuses edits/re-submit.
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # The receiving legal entity (our issuer profile) — an approval-policy dimension.
    legal_entity_id: Mapped[str | None] = mapped_column(GUID(), nullable=True, index=True)
    # GL account / category assignment at the invoice level (free-text; line items
    # carry their own `category`).
    account_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Who submitted for approval (segregation of duties: they cannot approve it).
    submitted_by: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Cost-allocation dimensions (free-text tags; see app.core.dimensions). Any
    # combination may be set; each is independently filterable/groupable.
    cost_center: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    department: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    project: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    vehicle: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    property_ref: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)

    # Slice 2: normalised links to the cost-allocation master tables (nullable
    # during the dual-read transition; the free-text tags above are the fallback
    # until a later slice deprecates them). Only the three dimensions that have a
    # master table are linked — vehicle/property_ref stay free-text for now.
    cost_center_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    department_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    project_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)

    # Data validation. status: none | passed | flagged | pending | approved | rejected.
    # findings is a JSON array of {severity, code, message, field}.
    validation_status: Mapped[str] = mapped_column(
        String(16), default="none", nullable=False, index=True
    )
    validation_findings: Mapped[str | None] = mapped_column(Text, nullable=True)
    validated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    vendor: Mapped[Vendor] = relationship(back_populates="invoices")
    line_items: Mapped[list[LineItem]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="LineItem.created_at",
    )


class LineItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "line_items"
    __table_args__ = (Index("ix_line_items_invoice_category", "invoice_id", "category"),)

    invoice_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(
        String(80), default="uncategorized", nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1"), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="line_items")
