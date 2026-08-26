"""WO-K AR legal trio: Art. 219 credit-note reference snapshot + the
statutory late-interest base-rate setting.

Revision ID: b5c7d9e1f3a5
Revises: a4b6c8d0e2f4
Create Date: 2026-08-26

Two columns, no new tables:

- `issued_invoices.corrected_invoice_number` — the corrected invoice's
  number snapshotted onto the credit note (Art. 219's unambiguous
  reference must live on the document, not behind a severable FK).
  Backfilled for existing credit notes from the still-standing row link.
- `organizations.late_interest_base_rate` — the admin-typed reference rate
  behind the advisory 2011/7/EU figure (NULL = service default constant).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5c7d9e1f3a5"
down_revision: Union[str, None] = "a4b6c8d0e2f4"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "issued_invoices",
        sa.Column("corrected_invoice_number", sa.String(64), nullable=True),
    )
    # Backfill: every existing credit note whose row link still stands gets the
    # number it corrects. A credit note whose original was since deleted (FK is
    # SET NULL) stays NULL — the reference is unrecoverable and inventing one
    # would be worse.
    op.execute(
        "UPDATE issued_invoices SET corrected_invoice_number = ("
        "SELECT o.number FROM issued_invoices o "
        "WHERE o.id = issued_invoices.corrected_invoice_id) "
        "WHERE doc_type = 'credit_note' AND corrected_invoice_id IS NOT NULL"
    )
    op.add_column(
        "organizations",
        sa.Column("late_interest_base_rate", sa.Numeric(5, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "late_interest_base_rate")
    op.drop_column("issued_invoices", "corrected_invoice_number")
