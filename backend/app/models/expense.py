from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

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
    event,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

Money = Numeric(14, 2)

# Lifecycle: draft → submitted → approved | rejected → reimbursed
EXPENSE_STATUSES = ("draft", "submitted", "approved", "rejected", "reimbursed")
EXPENSE_CATEGORIES = (
    "travel",
    "meals",
    "accommodation",
    "transport",
    "supplies",
    "software",
    "other",
)


class ExpenseReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An employee's expense claim — a set of receipted items submitted for
    approval and reimbursement. Tenant-scoped (org_id) and owned by an employee."""

    __tablename__ = "expense_reports"

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_name: Mapped[str] = mapped_column(String(200), nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    total: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    vat_total: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    total_eur: Mapped[Decimal | None] = mapped_column(Money, nullable=True)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list[ExpenseItem]] = relationship(
        back_populates="report", cascade="all, delete-orphan", order_by="ExpenseItem.spend_date"
    )


class ExpenseItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "expense_items"
    __table_args__ = (
        # Slice 2b: normalised links to the cost-allocation master tables, same
        # composite-FK tenant guard invoices use. Requires org_id on this table
        # (denormalised from the parent report — see the before_insert hook).
        ForeignKeyConstraint(
            ["org_id", "cost_center_id"],
            ["cost_centers.org_id", "cost_centers.id"],
            name="fk_expense_items_cost_center",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["org_id", "department_id"],
            ["departments.org_id", "departments.id"],
            name="fk_expense_items_department",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_expense_items_project",
            ondelete="SET NULL",
        ),
        Index("ix_expense_items_org_cost_center", "org_id", "cost_center_id"),
        Index("ix_expense_items_org_department", "org_id", "department_id"),
        Index("ix_expense_items_org_project", "org_id", "project_id"),
    )

    # Denormalised tenant key (mirrors the parent report's org_id). Makes the row
    # first-class tenant-scoped: the ORM guard + Postgres RLS can restrict it
    # directly instead of trusting the join, and the composite cost-allocation FKs
    # above can be tenant-safe. Populated automatically by the before_insert hook.
    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    report_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("expense_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    spend_date: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="other", nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    merchant: Mapped[str | None] = mapped_column(String(200), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)  # gross
    vat_amount: Mapped[Decimal] = mapped_column(
        Money, default=Decimal("0"), nullable=False
    )  # reclaimable
    payment_method: Mapped[str] = mapped_column(String(20), default="personal", nullable=False)
    comment: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # business purpose (Concur-style)

    # Cost-allocation dimensions (see app.core.dimensions) — e.g. tag fuel to a
    # vehicle, a site visit to a property, billable time to a project.
    cost_center: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    department: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    project: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    vehicle: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    property_ref: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)

    # Slice 2b: normalised links to the cost-allocation master tables (nullable
    # during the dual-read transition; the free-text tags above are the fallback).
    cost_center_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    department_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    project_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    # Bank-statement verification: a human-readable reference to the matched bank/
    # card transaction. Non-null ⇒ this entry is reconciled against the statement.
    bank_reference: Mapped[str | None] = mapped_column(String(300), nullable=True)

    receipt_mime: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Object-storage reference (ADR-0008): receipt bytes live in object storage,
    # keyed by sha256. The legacy in-DB blob was dropped once migrated.
    receipt_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    report: Mapped[ExpenseReport] = relationship(back_populates="items")


@event.listens_for(ExpenseItem, "before_insert")
def _fill_expense_item_org(mapper, connection, target: ExpenseItem) -> None:
    """Denormalise `org_id` from the parent report on insert, so every code path
    that creates an item — via the `report`/`items` relationship OR by setting
    `report_id` directly — gets it without having to remember.

    By the time this fires the FK (`report_id`) is always populated. We prefer the
    already-loaded parent (no query); otherwise we look the org up on the flush
    connection. `__dict__.get` reads the loaded relationship WITHOUT triggering a
    lazy load (which would fail on the async engine)."""
    if target.org_id is not None:
        return
    parent = target.__dict__.get("report")
    if parent is not None:
        target.org_id = parent.org_id
    elif target.report_id is not None:
        target.org_id = connection.scalar(
            select(ExpenseReport.org_id).where(ExpenseReport.id == target.report_id)
        )


class ExpenseTransaction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An 'available expense' — a card/bank-feed transaction in the employee's
    inbox (SAP Concur style). It sits unassigned until the employee adds it to a
    report, at which point it becomes an expense entry."""

    __tablename__ = "expense_transactions"
    # The 'available expenses' inbox filters by (employee, status) together.
    __table_args__ = (Index("ix_exp_txn_employee_status", "employee_id", "status"),)

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    merchant: Mapped[str | None] = mapped_column(String(200), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    direction: Mapped[str] = mapped_column(String(8), default="debit", nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="bank_statement", nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), default="available", nullable=False, index=True
    )  # available|assigned
    item_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("expense_items.id", ondelete="SET NULL"), nullable=True
    )


class ExpenseComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A comment on an expense report — the employee↔approver thread."""

    __tablename__ = "expense_comments"

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    report_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("expense_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    author_name: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
