"""Cost-allocation master data (Slice 1 of the logical data model).

Promotes the free-text cost-allocation *dimensions* (`core/dimensions.py`) to
first-class, tenant-scoped master entities: **Department**, **Cost center**,
**Project**. The free-text columns on invoices/expenses stay for now; a later
slice references these tables (composite FK) and backfills.

Design principles demonstrated here (see docs/architecture/data-model.md):
- **Tenant boundary:** every row has `org_id` (FK → organizations, RLS + ORM guard).
- **Cross-tenant FK protection:** `cost_centers.department_id` uses a **composite
  FK** `(org_id, department_id) → departments(org_id, id)`, so a cost center can
  only reference a department **in the same org** — structurally, not by hope.
- **Uniqueness / duplicate rules:** `code` is unique per org, per entity.
- **Status lifecycle + soft delete:** `status` (active|archived[/closed]) with
  `archived_at`; master data is archived, never hard-deleted (historical
  references must resolve).
- **Optimistic concurrency:** `version` is bumped on every update; a stale write
  is rejected (see `services/costing.update_*`).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class Department(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_departments_org_code"),
        # Target for the composite FK from cost_centers (enables cross-tenant-safe refs).
        UniqueConstraint("org_id", "id", name="uq_departments_org_id"),
        Index("ix_departments_org_status", "org_id", "status"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False
    )  # active|archived
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class CostCenter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cost_centers"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_cost_centers_org_code"),
        UniqueConstraint("org_id", "id", name="uq_cost_centers_org_id"),
        # Cross-tenant-safe reference: the department MUST belong to the same org.
        ForeignKeyConstraint(
            ["org_id", "department_id"],
            ["departments.org_id", "departments.id"],
            name="fk_cost_centers_department",
            ondelete="SET NULL",
        ),
        Index("ix_cost_centers_org_status", "org_id", "status"),
        Index("ix_cost_centers_org_department", "org_id", "department_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Optional roll-up to a department (same org, enforced by the composite FK).
    department_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False
    )  # active|archived
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_projects_org_code"),
        UniqueConstraint("org_id", "id", name="uq_projects_org_id"),
        Index("ix_projects_org_status", "org_id", "status"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="active", nullable=False
    )  # active|closed|archived
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The close-time P&L FREEZE (project-profitability phase 2): closing stores
    # the figure the client acted on; a late document becomes a visible
    # "arrived after close" adjustment instead of silent drift. Reopening
    # clears it (audited) — a reopened project is live again by definition.
    closed_pnl_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pnl_frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Acceptance & handover (WO-D, work-calendar/§5a): the customer's sign-off
    # that the work is done, recorded as a stamped EVENT on the project — not a
    # new status, so the active→closed lifecycle is untouched and acceptance
    # can precede closing by any interval. The document link points at a
    # project_documents row (typically kind='acceptance', generated from the
    # acceptance template or uploaded countersigned). Revoking clears all
    # three (audited) — e-sign is the later seam.
    # The customer this project is FOR (WO-E; WO-H/WO-I build on it). Same
    # reference style as ProjectAssignment.assignee_user_id: id validated
    # org-scoped in the service at write time, no DB-level FK — customers are
    # archived rather than deleted, and the composite-FK alternative cannot
    # express SET NULL without nulling org_id. Nullable: plenty of projects
    # (internal work) have no customer.
    customer_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    acceptance_document_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    acceptance_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
