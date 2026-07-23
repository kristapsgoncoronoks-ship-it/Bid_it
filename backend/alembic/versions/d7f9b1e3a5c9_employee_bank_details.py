"""users: employee bank details (IBAN/BIC) for reimbursement payouts

Additive nullable columns on the existing users table — no RLS/TENANT_TABLES
change. Used only when a reimbursement batch is exported as a SEPA credit-transfer.

Revision ID: d7f9b1e3a5c9
Revises: c5e7a9b1d3f7
Create Date: 2026-07-23 12:35:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7f9b1e3a5c9"
down_revision: Union[str, None] = "c5e7a9b1d3f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as b:
        b.add_column(sa.Column("iban", sa.String(length=34), nullable=True))
        b.add_column(sa.Column("bic", sa.String(length=11), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as b:
        b.drop_column("bic")
        b.drop_column("iban")
