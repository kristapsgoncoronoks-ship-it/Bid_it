"""interest_billed_through on issued_invoices — stop billing the same days twice

Revision ID: a1c93e7f2b48
Revises: d4c7b1e93f27
Create Date: 2026-08-12

A penalty invoice charged interest accrued from the DUE DATE every time it was
generated, because nothing recorded that any of it had been billed. Generating
twice produced two invoices for the same days — reproduced as €73.32 of interest
billed as €146.64 — and billing monthly multiplied it.

`interest_billed_through` is the date through which interest has been invoiced.
Billable accrual runs from max(due_date, interest_billed_through).

NULL is the correct value for every existing row: it means "none has been
billed", which restores the full accrual. It deliberately does NOT try to infer
a date from historic penalty invoices — those were generated under the broken
rule, so any inferred date would be a guess about which days they really
covered, and a wrong guess here silently forgives interest that is genuinely
owed. Existing over-billing is a commercial matter to settle with the customer,
not something a migration should paper over.
"""

import sqlalchemy as sa

from alembic import op

revision = "a1c93e7f2b48"
down_revision = "d4c7b1e93f27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "issued_invoices",
        sa.Column("interest_billed_through", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("issued_invoices", "interest_billed_through")
