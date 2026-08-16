"""Percentage allocation of one received invoice across projects (phase 2 of
docs/design/project-profitability.md).

The shared-cost reality in every industry: one supplier invoice covers many
jobs. The precedence rule, enforced in `project_profit`:

    line-level project > percentage split > the invoice's own project_id

A split allocates the invoice's REMAINDER — whatever its explicitly
project-tagged lines have not already claimed — and the rows for one invoice
must sum to exactly 100. Rounding residue lands on the largest share so the
parts always sum to the whole (never invent or lose a cent).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class InvoiceProjectSplit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invoice_project_splits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_invoice_project_splits_invoice",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_invoice_project_splits_project",
            ondelete="CASCADE",
        ),
        # One row per (invoice, project): a project's share is one number.
        UniqueConstraint(
            "org_id", "invoice_id", "project_id", name="uq_invoice_project_splits_pair"
        ),
        UniqueConstraint("org_id", "id", name="uq_invoice_project_splits_org_id"),
        Index("ix_invoice_project_splits_org_invoice", "org_id", "invoice_id"),
        Index("ix_invoice_project_splits_org_project", "org_id", "project_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    project_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
