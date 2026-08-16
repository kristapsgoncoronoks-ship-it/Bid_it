"""archive pre-expiry notice stamp + the paid retention extension

Revision ID: d0f3e5a1c7b9
Revises: c9e4f1a7b2d8
Create Date: 2026-08-16

The two halves of "nothing leaves the archive without the owner having been
told first" (docs/design/platform-archive.md; owner decisions 2026-08-15/16):

- `archived_invoices.expiry_notified_at` — one bit per record: has the notice
  covering it been sent. Cleared when an extension is granted, so a fresh
  notice precedes the NEW expiry too.
- `organizations.archive_retention_years` — the PAID extension's entitlement,
  NULL = the included 3 years. Operator-granted; billing wires up later.

Additive columns only: no table, no RLS change, no backfill (NULL is the
correct starting state for both).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "d0f3e5a1c7b9"
down_revision: Union[str, None] = "c9e4f1a7b2d8"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "archived_invoices",
        sa.Column("expiry_notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("archive_retention_years", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "archive_retention_years")
    op.drop_column("archived_invoices", "expiry_notified_at")
