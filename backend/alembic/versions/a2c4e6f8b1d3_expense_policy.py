"""expense spending policy (Phase 09)

One advisory spending policy per org (max item amount, per-category caps,
receipt-required threshold) used to flag out-of-policy items for approvers.
Tenant-scoped → RLS.

Revision ID: a2c4e6f8b1d3
Revises: f1a3c5e7b9d2
Create Date: 2026-07-22 21:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.models.base


revision: str = 'a2c4e6f8b1d3'
down_revision: Union[str, None] = 'f1a3c5e7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("expense_policies",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)
_TS = dict(server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False)


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "expense_policies",
        sa.Column("id", app.models.base.GUID(), nullable=False),
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("max_item_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("receipt_required_over", sa.Numeric(14, 2), nullable=True),
        sa.Column("category_caps", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", name="uq_expense_policies_org"),
    )
    op.create_index("ix_expense_policies_org_id", "expense_policies", ["org_id"])

    if is_pg:
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
    op.drop_table("expense_policies")
