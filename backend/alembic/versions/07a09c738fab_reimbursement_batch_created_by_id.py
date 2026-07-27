"""R6 — reimbursement batch maker id: additive column only.

`reimbursement_batches.created_by_id` — the immutable user id of the batch's
maker (creator), compared against the payer at `pay` time to enforce
maker≠checker (§4.8), mirroring `PaymentRun.created_by_id`. Additive column on
an EXISTING tenant table (no new table → no new RLS policy needed; the table's
existing FORCE ROW LEVEL SECURITY policy already covers every column).

Backfill semantics (deliberate, matching `d4e6f8a0b2c4`'s treatment of
`payment_runs.created_by_id`): NULL for every pre-existing batch — the
maker≠checker gate additionally compares the legacy `created_by` EMAIL, so a
pre-migration batch still cannot be paid by the account that created it.

Downgrade drops the column and therefore loses the id-based (vs. email-based)
attribution precision for batches created before this migration — never lossy
for money/ledger data (the batch's `total_eur`/`status`/reports are untouched).

Revision ID: 07a09c738fab
Revises: 1b4cf6cc802f
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import app.models.base  # portable GUID type used by every table

revision = "07a09c738fab"
down_revision = "1b4cf6cc802f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reimbursement_batches",
        sa.Column("created_by_id", app.models.base.GUID(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reimbursement_batches", "created_by_id")
