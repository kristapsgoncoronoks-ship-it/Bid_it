"""richer per-field extraction provenance

Adds honest-provenance columns to `extraction_fields`: the raw `original_value` vs
the cleaned `normalized_value`, a `reviewed_value` set when a human corrects the
capture, the `provider` that produced the field, and a `low_confidence` flag the
human-review queue keys on. All additive; `extraction_fields` is already an
RLS-scoped tenant table, so no policy change is needed. `low_confidence` defaults
FALSE via a portable boolean server_default (never an integer — Postgres rejects a
BOOLEAN DEFAULT 1).

Revision ID: b7e1c9a4d2f0
Revises: 955bd43a8cb3
Create Date: 2026-07-22 11:05:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e1c9a4d2f0"
down_revision: Union[str, None] = "955bd43a8cb3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("extraction_fields", schema=None) as batch_op:
        batch_op.add_column(sa.Column("original_value", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("normalized_value", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("reviewed_value", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("provider", sa.String(length=40), nullable=True))
        batch_op.add_column(
            sa.Column(
                "low_confidence", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("extraction_fields", schema=None) as batch_op:
        batch_op.drop_column("low_confidence")
        batch_op.drop_column("provider")
        batch_op.drop_column("reviewed_value")
        batch_op.drop_column("normalized_value")
        batch_op.drop_column("original_value")
