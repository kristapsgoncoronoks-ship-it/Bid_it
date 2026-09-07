"""Freeze Stripe metered-usage reporting segments (BILL-METER-001).

Revision ID: e7f9a1c3d5b8
Revises: c4d6e8f0a2b4

IMPACT: additive — one NOT NULL column with a server default on
`usage_counters`; no row is narrowed, dropped or rewritten beyond the backfill.
PREFLIGHT: none needed (no existing value can violate the new column).
NORMALIZATION: `reporting_target = reported` for every existing row — "no
segment in flight", which is exactly the state the old code left behind (it
never committed before the provider call).
POSTCONDITION: reported <= reporting_target for every row.
ROLLBACK: `downgrade` drops the column; the old delta algorithm needs nothing
else — PRECONDITION: no segment in flight, i.e.
`SELECT count(*) FROM usage_counters WHERE reported <> reporting_target` = 0,
otherwise those rows' pending quantities are re-exposed to the over-report the
column exists to prevent (run the usage job once on the new code first).
Lock: ADD COLUMN ... DEFAULT on Postgres 11+ is metadata-only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7f9a1c3d5b8"
down_revision: str | None = "c4d6e8f0a2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usage_counters",
        sa.Column("reporting_target", sa.Integer(), server_default="0", nullable=False),
    )
    # `reported` is already the acknowledged cumulative watermark; start with
    # no segment in flight.
    op.execute(sa.text("UPDATE usage_counters SET reporting_target = reported"))


def downgrade() -> None:
    op.drop_column("usage_counters", "reporting_target")
