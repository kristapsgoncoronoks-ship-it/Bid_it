"""extraction lineage — capture provenance for received invoices (Slice 5b)

One row per capture attempt at the parse choke point (method / source / status),
linked to the invoice when the reviewed draft is saved. The link is a COMPOSITE
FK `(org_id, invoice_id) → invoices(org_id, id)` (tenant-safe), so the unique
constraint on the parent is created FIRST (Postgres requires it before the FK).

Revision ID: e7da509694a5
Revises: c591d32e283a
Create Date: 2026-07-21 13:51:56.042926
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "e7da509694a5"
down_revision: Union[str, None] = "c591d32e283a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("extraction_runs",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    # 1) The composite-FK target must be unique BEFORE the FK references it.
    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_invoices_org_id", ["org_id", "id"])

    # 2) The lineage table.
    op.create_table(
        "extraction_runs",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("invoice_id", app.models.base.GUID(), nullable=True),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("method", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="parsed"),
        sa.Column("field_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("id", app.models.base.GUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_extraction_runs_invoice",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_extraction_runs_org_created", "extraction_runs", ["org_id", "created_at"], unique=False
    )
    op.create_index(op.f("ix_extraction_runs_org_id"), "extraction_runs", ["org_id"], unique=False)
    op.create_index(
        "ix_extraction_runs_org_invoice", "extraction_runs", ["org_id", "invoice_id"], unique=False
    )

    # 3) Postgres RLS.
    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_index("ix_extraction_runs_org_invoice", table_name="extraction_runs")
    op.drop_index(op.f("ix_extraction_runs_org_id"), table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_org_created", table_name="extraction_runs")
    op.drop_table("extraction_runs")
    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.drop_constraint("uq_invoices_org_id", type_="unique")
