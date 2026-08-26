"""WO-G phase 2: supplier agreed prices + the org overcharge-block toggle.

Revision ID: a4b6c8d0e2f4
Revises: f6a8b0c2d4e6
Create Date: 2026-08-25

One tenant table (docs/design/supplier-cost-analytics.md §2 phase 2) with
ENABLE+FORCE RLS here and registry + parity probe in the same commit, plus
`organizations.overcharge_block_enabled` (default FALSE — the design's open
question 2 resolved as advisory-by-default, block per org opt-in).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision: str = "a4b6c8d0e2f4"
down_revision: Union[str, None] = "f6a8b0c2d4e6"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TENANT_TABLES = ("supplier_agreed_prices",)


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "supplier_agreed_prices",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id",
            GUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vendor_id", GUID(), nullable=False),
        sa.Column("item", sa.String(500), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("agreed_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_by", sa.String(320), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_supplier_agreed_prices_org_id"),
        sa.UniqueConstraint(
            "org_id",
            "vendor_id",
            "item",
            "currency",
            "valid_from",
            name="uq_supplier_agreed_prices_entry",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "vendor_id"],
            ["vendors.org_id", "vendors.id"],
            name="fk_supplier_agreed_prices_vendor",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_supplier_agreed_prices_org_id", "supplier_agreed_prices", ["org_id"])
    op.create_index(
        "ix_supplier_agreed_prices_org_vendor", "supplier_agreed_prices", ["org_id", "vendor_id"]
    )

    op.add_column(
        "organizations",
        sa.Column(
            "overcharge_block_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

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
    op.drop_column("organizations", "overcharge_block_enabled")
    op.drop_index(
        "ix_supplier_agreed_prices_org_vendor", table_name="supplier_agreed_prices"
    )
    op.drop_index("ix_supplier_agreed_prices_org_id", table_name="supplier_agreed_prices")
    op.drop_table("supplier_agreed_prices")
