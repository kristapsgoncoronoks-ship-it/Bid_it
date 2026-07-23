"""vendor bank details (Phase 17)

Adds `vendors.iban` / `vendors.bic` — the creditor account a SEPA pain.001 credit
transfer pays into (AP payment-file export). Additive nullable columns; no RLS
change (vendors is already a tenant table).

Revision ID: a2c4e6b8d1f3
Revises: f1b3d5e7a9c2
Create Date: 2026-07-23 08:15:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2c4e6b8d1f3"
down_revision: Union[str, None] = "f1b3d5e7a9c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("vendors", schema=None) as batch_op:
        batch_op.add_column(sa.Column("iban", sa.String(length=34), nullable=True))
        batch_op.add_column(sa.Column("bic", sa.String(length=11), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("vendors", schema=None) as batch_op:
        batch_op.drop_column("bic")
        batch_op.drop_column("iban")
