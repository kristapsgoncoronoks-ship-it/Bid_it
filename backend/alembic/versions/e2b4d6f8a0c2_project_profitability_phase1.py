"""project profitability phase 1 — revenue link, contract file, cost lines

Revision ID: e2b4d6f8a0c2
Revises: d0f3e5a1c7b9
Create Date: 2026-08-16

Phase 1 of docs/design/project-profitability.md (industry-neutral by owner
requirement — nothing here names an industry):

- `issued_invoices.project_id` — the REVENUE side of the project P&L, the
  column whose absence made profitability uncomputable. Composite tenant FK,
  SET NULL (deleting a project must never take issued legal documents along).
- `project_documents` — the signed contract attached to the project.
- `project_cost_entries` — manual uninvoiced costs (wages, per diems,
  equipment hire). Deliberately NOT payroll.

Both new tables are tenant tables: registered in TENANT_MODELS (layer 2),
probed in test_tenancy_parity (registry), and given ENABLE + FORCE row-level
security with the tenant_isolation policy here (layer 3) — the archived_
invoices migration learned the hard way that shipping layers 1+2 without 3
fails the RLS coverage test.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2b4d6f8a0c2"
down_revision: Union[str, None] = "d0f3e5a1c7b9"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TENANT_TABLES = ("project_documents", "project_cost_entries")


def _uuid() -> sa.types.TypeEngine:
    return sa.String(36)


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("issued_invoices", sa.Column("project_id", _uuid(), nullable=True))
    with op.batch_alter_table("issued_invoices") as batch:
        batch.create_foreign_key(
            "fk_issued_invoices_project",
            "projects",
            ["org_id", "project_id"],
            ["org_id", "id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_issued_org_project", "issued_invoices", ["org_id", "project_id"])

    op.create_table(
        "project_documents",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("org_id", _uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", _uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="contract"),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=True),
        sa.Column("uploaded_by", sa.String(320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_project_documents_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_project_documents_org_id"),
        sa.CheckConstraint("kind IN ('contract', 'other')", name="ck_project_documents_kind"),
    )
    op.create_index("ix_project_documents_org_project", "project_documents", ["org_id", "project_id"])
    op.create_index("ix_project_documents_org_id", "project_documents", ["org_id"])

    op.create_table(
        "project_cost_entries",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("org_id", _uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", _uuid(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("category", sa.String(16), nullable=False, server_default="other"),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("entry_date", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_project_cost_entries_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_project_cost_entries_org_id"),
        sa.CheckConstraint(
            "category IN ('wages', 'per_diem', 'equipment', 'other')",
            name="ck_project_cost_entries_category",
        ),
    )
    op.create_index(
        "ix_project_cost_entries_org_project", "project_cost_entries", ["org_id", "project_id"]
    )
    op.create_index("ix_project_cost_entries_org_id", "project_cost_entries", ["org_id"])

    if bind.dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            # FORCE so the policy applies to the table OWNER too — the app's
            # connection frequently IS the owner, and without FORCE the layer
            # exists in the schema and not in practice.
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                "USING (org_id = current_setting('app.current_org', true)::varchar)"
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_index("ix_project_cost_entries_org_id", table_name="project_cost_entries")
    op.drop_index("ix_project_cost_entries_org_project", table_name="project_cost_entries")
    op.drop_table("project_cost_entries")
    op.drop_index("ix_project_documents_org_id", table_name="project_documents")
    op.drop_index("ix_project_documents_org_project", table_name="project_documents")
    op.drop_table("project_documents")
    op.drop_index("ix_issued_org_project", table_name="issued_invoices")
    with op.batch_alter_table("issued_invoices") as batch:
        batch.drop_constraint("fk_issued_invoices_project", type_="foreignkey")
    op.drop_column("issued_invoices", "project_id")
