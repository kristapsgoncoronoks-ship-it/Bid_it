"""N1 — the received invoice keeps the supplier's purchase-order reference (BT-13)

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-09-13

`issued_invoices.po_reference` has carried EN-16931 BT-13 since the issuing
module shipped — the PO reference a BUYER quotes so they can match our invoice
to their order. The receiving side had no such column, so when a supplier sent
us the same field in the same standard, the parser read past it and the number
was gone. Without it there is nothing to match a supplier invoice to a purchase
order, which is the first thing an AP approver asks for.

Additive and nullable: every existing row simply has no PO reference, which is
the truth about them — nothing is backfilled or guessed. Same width as the
issued column (60) so the field survives a round trip through either side.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("po_reference", sa.String(60), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "po_reference")
