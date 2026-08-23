"""project profitability phase 2 — allocation + the close-time freeze

Revision ID: f3c5e7a9b1d4
Revises: e2b4d6f8a0c2
Create Date: 2026-08-16

- `line_items.project_id` — line-level allocation; a line's explicit project
  wins over everything. Plain GUID (line_items has no org_id; it is reachable
  only through its org-scoped invoice, and the service validates same-org).
- `invoice_project_splits` — percentage allocation of an invoice's remainder
  across projects; rows per invoice must sum to 100. TENANT table: registered,
  probed, and ENABLE+FORCE RLS here (all three layers in one commit).
- `projects.closed_pnl_json` + `projects.pnl_frozen_at` — the close-time
  freeze: the figure the client acted on, stored; late documents become
  visible adjustments instead of silent drift.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision: str = "f3c5e7a9b1d4"
down_revision: Union[str, None] = "e2b4d6f8a0c2"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TENANT_TABLES = ("invoice_project_splits",)


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("line_items", sa.Column("project_id", GUID(), nullable=True))
    op.create_index("ix_line_items_project_id", "line_items", ["project_id"])

    op.add_column("projects", sa.Column("closed_pnl_json", sa.Text(), nullable=True))
    op.add_column(
        "projects", sa.Column("pnl_frozen_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "invoice_project_splits",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id",
            GUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("invoice_id", GUID(), nullable=False),
        sa.Column("project_id", GUID(), nullable=False),
        sa.Column("percent", sa.Numeric(5, 2), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_invoice_project_splits_invoice",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_invoice_project_splits_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "org_id", "invoice_id", "project_id", name="uq_invoice_project_splits_pair"
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_invoice_project_splits_org_id"),
    )
    op.create_index(
        "ix_invoice_project_splits_org_invoice", "invoice_project_splits", ["org_id", "invoice_id"]
    )
    op.create_index(
        "ix_invoice_project_splits_org_project", "invoice_project_splits", ["org_id", "project_id"]
    )
    op.create_index("ix_invoice_project_splits_org_id", "invoice_project_splits", ["org_id"])

    if bind.dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                f"USING (current_setting('app.current_org', true) IS NULL OR org_id::text = current_setting('app.current_org', true)) WITH CHECK (current_setting('app.current_org', true) IS NULL OR org_id::text = current_setting('app.current_org', true))"
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_index("ix_invoice_project_splits_org_id", table_name="invoice_project_splits")
    op.drop_index("ix_invoice_project_splits_org_project", table_name="invoice_project_splits")
    op.drop_index("ix_invoice_project_splits_org_invoice", table_name="invoice_project_splits")
    op.drop_table("invoice_project_splits")
    op.drop_column("projects", "pnl_frozen_at")
    op.drop_column("projects", "closed_pnl_json")
    op.drop_index("ix_line_items_project_id", table_name="line_items")
    op.drop_column("line_items", "project_id")
