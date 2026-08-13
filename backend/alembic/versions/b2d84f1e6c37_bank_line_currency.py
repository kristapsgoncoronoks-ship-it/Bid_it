"""bank_lines.currency — keep the currency the bank stated

Revision ID: b2d84f1e6c37
Revises: a1c93e7f2b48
Create Date: 2026-08-12

camt.053 states a currency per entry in `Amt/@Ccy`. The parser now reads it, but
`bank_lines` had nowhere to put it, so a USD credit was stored as a bare number
indistinguishable from euros and could be matched to — and settle — a EUR
invoice of the same magnitude.

NULL means the SOURCE did not state a currency (a CSV or PDF statement usually
does not), which is different from "it is the org's currency". Nothing back-fills
a value: rows imported before this column existed genuinely do not record what
currency they were in, and inventing one would be the same guess this column
exists to prevent.
"""

import sqlalchemy as sa

from alembic import op

revision = "b2d84f1e6c37"
down_revision = "a1c93e7f2b48"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bank_lines", sa.Column("currency", sa.String(length=3), nullable=True))


def downgrade() -> None:
    op.drop_column("bank_lines", "currency")
