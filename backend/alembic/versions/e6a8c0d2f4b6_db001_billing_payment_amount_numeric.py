"""DB-001 — `billing_payments.amount_eur` from Float to Numeric(14, 2).

The only float money column in the schema. It is the server-side record a
redirect-flow (EveryPay) payment result is verified against, so an inexact
representation is a verification that can fail on a correct payment, and a
sum over the table that drifts. Every other amount in the schema is
Numeric(14, 2); this column joins them.

Existing values are rounded to two decimals on the way across (`USING
round(amount_eur::numeric, 2)` on Postgres) — every value ever written came
from an integer plan price, so nothing is lost; SQLite is typeless and the
batch rebuild carries the values through unchanged.

Revision ID: e6a8c0d2f4b6
Revises: d4f6a8b0c2e4
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e6a8c0d2f4b6"
down_revision: str | None = "d4f6a8b0c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("billing_payments", schema=None) as batch_op:
        batch_op.alter_column(
            "amount_eur",
            existing_type=sa.Float(),
            type_=sa.Numeric(14, 2),
            existing_nullable=False,
            postgresql_using="round(amount_eur::numeric, 2)",
        )


def downgrade() -> None:
    with op.batch_alter_table("billing_payments", schema=None) as batch_op:
        batch_op.alter_column(
            "amount_eur",
            existing_type=sa.Numeric(14, 2),
            type_=sa.Float(),
            existing_nullable=False,
        )
