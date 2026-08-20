"""project lifecycle phase 4 — offers/estimates + the invoicing plan

Revision ID: a5b7c9d1e3f5
Revises: f3c5e7a9b1d4
Create Date: 2026-08-16

docs/design/project-profitability.md §5a, owner decisions of 2026-08-16:
- `project_offers` — versionable offers with CLIENT-SET numbering
  (organizations.offer_prefix; the platform enforces per-org uniqueness only).
  Lines as JSON, the archive's pattern.
- `invoicing_plan_rows` — the contracted schedule, tracked against what was
  actually issued.
Both tenant tables: registered, probed, ENABLE+FORCE RLS here.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a5b7c9d1e3f5"
down_revision: Union[str, None] = "f3c5e7a9b1d4"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TENANT_TABLES = ("project_offers", "invoicing_plan_rows")


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("organizations", sa.Column("offer_prefix", sa.String(20), nullable=True))

    op.create_table(
        "project_offers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("number", sa.String(60), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("total", sa.Numeric(14, 2), nullable=False),
        sa.Column("line_items_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(320), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_project_offers_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("org_id", "number", "version", name="uq_project_offers_number_version"),
        sa.UniqueConstraint("org_id", "id", name="uq_project_offers_org_id"),
    )
    op.create_index("ix_project_offers_org_project", "project_offers", ["org_id", "project_id"])
    op.create_index("ix_project_offers_org_id", "project_offers", ["org_id"])

    op.create_table(
        "invoicing_plan_rows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_invoicing_plan_rows_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_invoicing_plan_rows_org_id"),
    )
    op.create_index(
        "ix_invoicing_plan_rows_org_project", "invoicing_plan_rows", ["org_id", "project_id"]
    )
    op.create_index("ix_invoicing_plan_rows_org_id", "invoicing_plan_rows", ["org_id"])

    if bind.dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
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
    op.drop_index("ix_invoicing_plan_rows_org_id", table_name="invoicing_plan_rows")
    op.drop_index("ix_invoicing_plan_rows_org_project", table_name="invoicing_plan_rows")
    op.drop_table("invoicing_plan_rows")
    op.drop_index("ix_project_offers_org_id", table_name="project_offers")
    op.drop_index("ix_project_offers_org_project", table_name="project_offers")
    op.drop_table("project_offers")
    op.drop_column("organizations", "offer_prefix")
