"""WO-P (R19) — the getting-started checklist's one persisted bit.

The checklist itself is derived (`services/onboarding.py` computes every step
from rows that already exist — no new tables, per the plan). Dismissal is the
single fact the derivation cannot reconstruct, so it is a nullable stamp on
the organization row: NULL = never dismissed.

Revision ID: e8f0a2b4c6d8
Revises: d7e9f1a3b5c7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8f0a2b4c6d8"
down_revision: str | None = "d7e9f1a3b5c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("onboarding_dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "onboarding_dismissed_at")
