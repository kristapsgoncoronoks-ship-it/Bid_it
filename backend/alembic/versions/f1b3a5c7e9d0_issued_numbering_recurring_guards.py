"""issued invoices: numbering + recurring concurrency guards

- Unique (org_id, number): a DB backstop that makes duplicate invoice/credit-note
  numbers impossible even under a numbering race.
- recurring_id + recurring_period columns + unique (org_id, recurring_id,
  recurring_period): at most one invoice per (schedule, run date), so overlapping
  recurring workers cannot double-generate an occurrence.

Additive columns/constraints on the existing (tenant-scoped, RLS-covered)
issued_invoices table — no RLS/TENANT_TABLES change.

Revision ID: f1b3a5c7e9d0
Revises: e9c1a3b5d7f4
Create Date: 2026-07-23 14:30:00.000000
"""

from typing import Sequence, Union

import app.models.base
import sqlalchemy as sa
from alembic import op

revision: str = "f1b3a5c7e9d0"
down_revision: Union[str, None] = "e9c1a3b5d7f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("issued_invoices", schema=None) as b:
        b.add_column(sa.Column("recurring_id", app.models.base.GUID(), nullable=True))
        b.add_column(sa.Column("recurring_period", sa.Date(), nullable=True))
        b.create_index("ix_issued_invoices_recurring_id", ["recurring_id"], unique=False)
        b.create_unique_constraint("uq_issued_invoices_org_number", ["org_id", "number"])
        b.create_unique_constraint(
            "uq_issued_invoices_recurring_period",
            ["org_id", "recurring_id", "recurring_period"],
        )


def downgrade() -> None:
    with op.batch_alter_table("issued_invoices", schema=None) as b:
        b.drop_constraint("uq_issued_invoices_recurring_period", type_="unique")
        b.drop_constraint("uq_issued_invoices_org_number", type_="unique")
        b.drop_index("ix_issued_invoices_recurring_id")
        b.drop_column("recurring_period")
        b.drop_column("recurring_id")
