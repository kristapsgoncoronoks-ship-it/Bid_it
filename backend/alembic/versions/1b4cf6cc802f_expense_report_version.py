"""expense report version (WO-30/R4)

Adds `expense_reports.version` — optimistic concurrency for the approval
`/decision` route (approve/reject/return_for_correction/mark_for_reimbursement/
mark_reimbursed, one handler). Existing rows backfill to `1` via the server
default; no existing data is modified. Mirrors `invoices.version` /
`reimbursement_batches.version`.

Revision ID: 1b4cf6cc802f
Revises: 6fec8c88ba7c
Create Date: 2026-07-27 21:40:54.523383
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1b4cf6cc802f"
down_revision: str | None = "6fec8c88ba7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "expense_reports",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("expense_reports", "version")
