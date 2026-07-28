"""dogfood subscription billing columns (H1.6 / WO-48)

InvoiceIQ invoicing its own paying tenants through the platform's OWN AR
module when no live billing provider is configured. Adds two nullable
columns to the EXISTING `issued_invoices` table — `subscription_org_id`
(which OTHER tenant this invoice bills) and `subscription_period` (the
calendar month it covers) — plus a unique constraint on
(org_id, subscription_org_id, subscription_period) so a re-run of the daily
generation job can never double-invoice a tenant for the same month. Both
columns are NULL on every pre-existing row (they were never subscription
invoices) and stay NULL on every ordinary invoice going forward — no
backfill needed. No new tenant table, so no new RLS policy: `issued_invoices`
already carries one from its original migration.

Revision ID: 5b295223466f
Revises: 01160bebbd7f
Create Date: 2026-07-28 17:59:00.293685
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import app.models.base  # portable GUID type used by every table

revision: str = "5b295223466f"
down_revision: Union[str, None] = "01160bebbd7f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_G = app.models.base.GUID


def upgrade() -> None:
    with op.batch_alter_table("issued_invoices", schema=None) as b:
        b.add_column(sa.Column("subscription_org_id", _G(), nullable=True))
        b.add_column(sa.Column("subscription_period", sa.Date(), nullable=True))
        b.create_index(
            "ix_issued_invoices_subscription_org_id", ["subscription_org_id"], unique=False
        )
        b.create_unique_constraint(
            "uq_issued_invoices_subscription_period",
            ["org_id", "subscription_org_id", "subscription_period"],
        )
        b.create_foreign_key(
            "fk_issued_invoices_subscription_org",
            "organizations",
            ["subscription_org_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("issued_invoices", schema=None) as b:
        b.drop_constraint("fk_issued_invoices_subscription_org", type_="foreignkey")
        b.drop_constraint("uq_issued_invoices_subscription_period", type_="unique")
        b.drop_index("ix_issued_invoices_subscription_org_id")
        b.drop_column("subscription_period")
        b.drop_column("subscription_org_id")
