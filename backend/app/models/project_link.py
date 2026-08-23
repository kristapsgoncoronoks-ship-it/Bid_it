"""Project-scoped records: the contract file and manual cost lines.

Phase 1 of `docs/design/project-profitability.md`. Both tables are deliberately
INDUSTRY-NEUTRAL (owner requirement): nothing here names a vehicle, cargo,
site or crew — a project is a won piece of work, whatever the business does.

Two small tables rather than columns on `projects`:

- `project_documents` — the signed contract (and other papers) attached to the
  project. Bytes live in the content-addressed object store like every other
  document class; the row is the link. The e-sign integration is a later phase
  and plugs into this slot.
- `project_cost_entries` — costs that never arrive as an invoice or an expense
  report: wages for the job, per diems, equipment hire. EXPLICITLY NOT PAYROLL
  (owner decision 2026-08-16): a wage line is a labelled amount that makes the
  P&L honest, with no employee master, no tax, no net/gross semantics anywhere.
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

Money = Numeric(14, 2)

# The closed set of manual-cost categories. Generic by design — "wages" is the
# same word for a driver, a site crew or a field crew.
COST_CATEGORIES = ("wages", "per_diem", "equipment", "other")
_CATEGORY_CHECK = "category IN ('wages', 'per_diem', 'equipment', 'other')"

DOCUMENT_KINDS = ("contract", "acceptance", "other")
_KIND_CHECK = "kind IN ('contract', 'acceptance', 'other')"


class ProjectDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A document attached to a project — typically the signed contract."""

    __tablename__ = "project_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_project_documents_project",
            ondelete="CASCADE",
        ),
        UniqueConstraint("org_id", "id", name="uq_project_documents_org_id"),
        Index("ix_project_documents_org_project", "org_id", "project_id"),
        CheckConstraint(_KIND_CHECK, name="ck_project_documents_kind"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="contract", nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class ProjectCostEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One uninvoiced cost booked onto a project, by hand.

    EUR for now (`currency` exists for the multi-currency extension but is
    constrained to EUR until the FX treatment is decided with the phase-2
    allocation work — a wrong number in a foreign currency is worse than a
    refused one).
    """

    __tablename__ = "project_cost_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_project_cost_entries_project",
            ondelete="CASCADE",
        ),
        UniqueConstraint("org_id", "id", name="uq_project_cost_entries_org_id"),
        Index("ix_project_cost_entries_org_project", "org_id", "project_id"),
        CheckConstraint(_CATEGORY_CHECK, name="ck_project_cost_entries_category"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(16), default="other", nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    entry_date: Mapped[date_type | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
