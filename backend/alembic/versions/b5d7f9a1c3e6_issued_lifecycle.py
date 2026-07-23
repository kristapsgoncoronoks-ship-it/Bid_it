"""issued invoices: explicit draft/approved lifecycle + dispute/write-off/viewed

Adds a stored `lifecycle` (default 'issued' = the historical born-final
behaviour), delivery/dispute/write-off timestamps, and makes `number` nullable so
a DRAFT (no gap-free number until issued) can exist. Additive on the existing
RLS-covered issued_invoices table.

Revision ID: b5d7f9a1c3e6
Revises: a3c5e7b9d1f4
Create Date: 2026-07-23 15:15:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5d7f9a1c3e6"
down_revision: Union[str, None] = "a3c5e7b9d1f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DT = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("issued_invoices", schema=None) as b:
        b.add_column(
            sa.Column("lifecycle", sa.String(length=16), nullable=False, server_default="issued")
        )
        b.add_column(sa.Column("viewed_at", _DT, nullable=True))
        b.add_column(sa.Column("disputed_at", _DT, nullable=True))
        b.add_column(sa.Column("dispute_reason", sa.String(length=300), nullable=True))
        b.add_column(sa.Column("written_off_at", _DT, nullable=True))
        b.add_column(sa.Column("writeoff_reason", sa.String(length=300), nullable=True))
        b.add_column(sa.Column("approved_at", _DT, nullable=True))
        b.add_column(sa.Column("issued_at", _DT, nullable=True))
        b.alter_column("number", existing_type=sa.String(length=40), nullable=True)
        b.create_index("ix_issued_invoices_lifecycle", ["lifecycle"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("issued_invoices", schema=None) as b:
        b.drop_index("ix_issued_invoices_lifecycle")
        b.alter_column("number", existing_type=sa.String(length=40), nullable=False)
        b.drop_column("issued_at")
        b.drop_column("approved_at")
        b.drop_column("writeoff_reason")
        b.drop_column("written_off_at")
        b.drop_column("dispute_reason")
        b.drop_column("disputed_at")
        b.drop_column("viewed_at")
        b.drop_column("lifecycle")
