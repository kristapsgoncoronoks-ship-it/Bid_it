"""WO-9 — payment-run controls: maker≠checker, export-once, traceable MsgId.

Additive columns only, on two EXISTING tenant tables (no new table → no new RLS
policy needed; the tables' existing FORCE ROW LEVEL SECURITY policies already
cover every column):

`payment_runs`
- `created_by_id`, `approved_by`, `approved_by_id`, `approved_at` — the maker and
  the checker, recorded by immutable user id (email columns are display copies).
  The run lifecycle gains an `approved` status between `open` and `paid`; the
  status column is a plain VARCHAR, so no constraint change is needed.
- `exported_at`, `export_count`, `last_msg_id` — export-once semantics: when the
  first bank file was produced, how many were produced, and the last pain.001
  MsgId emitted (each export's MsgId is also in the append-only audit log, so a
  bank query about ANY historic message id can still be answered).

`reimbursement_batches`
- `exported_at`, `export_count`, `last_msg_id` — the same export-once treatment
  for the employee payout rail.

Backfill semantics (deliberate):
- `export_count = 0`, `exported_at = NULL` for every existing run/batch: they are
  treated as NEVER exported, so a file that was in fact already sent to the bank
  before this deploy can be re-exported ONCE without the confirm flag. Flag this
  in the deploy note to the operator.
- `created_by_id`/`approved_by_id` are NULL for existing runs: the maker≠checker
  gate additionally compares the legacy `created_by` EMAIL, so a pre-migration
  run still cannot be paid by the account that created it.
- Existing OPEN runs must now be APPROVED by a second user before they can be
  paid — deliberate: that approval is the whole control.

Downgrade drops the new columns and therefore LOSES the export history and the
maker/checker attribution. Never downgrade in production without first extracting
`payment_runs.last_msg_id` / `reimbursement_batches.last_msg_id` (and the
`payment_run.exported` audit rows) to a CSV — the MsgId values may be needed to
trace a payment with the bank.

Revision ID: d4e6f8a0b2c4
Revises: b1c3e5a7f9d1
Create Date: 2026-07-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import app.models.base  # portable GUID type used by every table

revision = "d4e6f8a0b2c4"
down_revision = "b1c3e5a7f9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_runs", sa.Column("created_by_id", app.models.base.GUID(), nullable=True)
    )
    op.add_column("payment_runs", sa.Column("approved_by", sa.String(length=200), nullable=True))
    op.add_column(
        "payment_runs", sa.Column("approved_by_id", app.models.base.GUID(), nullable=True)
    )
    op.add_column("payment_runs", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payment_runs", sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "payment_runs",
        sa.Column("export_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("payment_runs", sa.Column("last_msg_id", sa.String(length=35), nullable=True))

    op.add_column(
        "reimbursement_batches", sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "reimbursement_batches",
        sa.Column("export_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "reimbursement_batches", sa.Column("last_msg_id", sa.String(length=35), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("reimbursement_batches", "last_msg_id")
    op.drop_column("reimbursement_batches", "export_count")
    op.drop_column("reimbursement_batches", "exported_at")

    op.drop_column("payment_runs", "last_msg_id")
    op.drop_column("payment_runs", "export_count")
    op.drop_column("payment_runs", "exported_at")
    op.drop_column("payment_runs", "approved_at")
    op.drop_column("payment_runs", "approved_by_id")
    op.drop_column("payment_runs", "approved_by")
    op.drop_column("payment_runs", "created_by_id")
