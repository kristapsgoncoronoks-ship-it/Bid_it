"""expense management: per-item FX/tax/mileage/per-diem/billable + policy rules

Additive columns only — no new tables, so no RLS/TENANT_TABLES change (the target
tables `expense_items` / `expense_policies` are already tenant-scoped with RLS).

- expense_items: original-currency + FX provenance, reclaimable-tax flag,
  customer-billable, entry kind (standard|mileage|per_diem) with mileage/per-diem
  inputs, and a missing-receipt declaration.
- expense_policies: configurable review rules (category/currency allow-lists,
  weekend + duplicate + late-submission + mileage-rate checks, per-item
  business-purpose threshold, and the block-vs-warn rule list).

Revision ID: b4d6f8a0c2e4
Revises: a2c4e6b8d1f3
Create Date: 2026-07-23 09:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4d6f8a0c2e4"
down_revision: Union[str, None] = "a2c4e6b8d1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("expense_items", schema=None) as b:
        b.add_column(sa.Column("currency", sa.String(length=3), nullable=True))
        b.add_column(sa.Column("original_amount", sa.Numeric(precision=14, scale=2), nullable=True))
        b.add_column(sa.Column("fx_rate", sa.Numeric(precision=18, scale=8), nullable=True))
        b.add_column(sa.Column("fx_source", sa.String(length=40), nullable=True))
        b.add_column(
            sa.Column("reclaimable_tax", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        b.add_column(
            sa.Column("customer_billable", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        b.add_column(sa.Column("billable_customer", sa.String(length=200), nullable=True))
        b.add_column(
            sa.Column(
                "expense_type",
                sa.String(length=16),
                nullable=False,
                server_default="standard",
            )
        )
        b.add_column(
            sa.Column("mileage_distance", sa.Numeric(precision=12, scale=2), nullable=True)
        )
        b.add_column(sa.Column("mileage_rate", sa.Numeric(precision=12, scale=4), nullable=True))
        b.add_column(sa.Column("mileage_unit", sa.String(length=4), nullable=True))
        b.add_column(sa.Column("per_diem_days", sa.Numeric(precision=8, scale=2), nullable=True))
        b.add_column(sa.Column("per_diem_rate", sa.Numeric(precision=14, scale=2), nullable=True))
        b.add_column(sa.Column("missing_receipt_declaration", sa.String(length=500), nullable=True))

    with op.batch_alter_table("expense_policies", schema=None) as b:
        b.add_column(sa.Column("allowed_categories", sa.Text(), nullable=True))
        b.add_column(sa.Column("allowed_currencies", sa.Text(), nullable=True))
        b.add_column(
            sa.Column("warn_weekend", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        b.add_column(
            sa.Column("duplicate_detection", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        b.add_column(sa.Column("mileage_rate", sa.Numeric(precision=12, scale=4), nullable=True))
        b.add_column(
            sa.Column("mileage_rate_tolerance", sa.Numeric(precision=12, scale=4), nullable=True)
        )
        b.add_column(
            sa.Column("require_purpose_over", sa.Numeric(precision=14, scale=2), nullable=True)
        )
        b.add_column(sa.Column("late_submission_days", sa.Integer(), nullable=True))
        b.add_column(sa.Column("blocking_rules", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("expense_policies", schema=None) as b:
        for col in (
            "blocking_rules",
            "late_submission_days",
            "require_purpose_over",
            "mileage_rate_tolerance",
            "mileage_rate",
            "duplicate_detection",
            "warn_weekend",
            "allowed_currencies",
            "allowed_categories",
        ):
            b.drop_column(col)

    with op.batch_alter_table("expense_items", schema=None) as b:
        for col in (
            "missing_receipt_declaration",
            "per_diem_rate",
            "per_diem_days",
            "mileage_unit",
            "mileage_rate",
            "mileage_distance",
            "expense_type",
            "billable_customer",
            "customer_billable",
            "reclaimable_tax",
            "fx_source",
            "fx_rate",
            "original_amount",
            "currency",
        ):
            b.drop_column(col)
