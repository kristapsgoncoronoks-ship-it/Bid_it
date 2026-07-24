"""issued invoice lifecycle — sent + void (Phase 10)

Adds delivery + cancellation to issued invoices: sent_at (first email delivery),
voided_at + void_reason (cancel an unpaid invoice). Additive nullable columns on
an existing RLS-covered tenant table.

Revision ID: b3d5f7a9c1e4
Revises: a2c4e6f8b1d3
Create Date: 2026-07-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3d5f7a9c1e4'
down_revision: Union[str, None] = 'a2c4e6f8b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("issued_invoices", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "issued_invoices", sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("issued_invoices", sa.Column("void_reason", sa.String(length=300), nullable=True))


def downgrade() -> None:
    for col in ("void_reason", "voided_at", "sent_at"):
        op.drop_column("issued_invoices", col)
