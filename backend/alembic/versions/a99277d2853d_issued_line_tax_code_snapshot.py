"""issued line tax_code snapshot (Slice 4b)

Adds a nullable `tax_code` snapshot label to issued invoice lines — the catalogue
code chosen at issue time (which drives the line's vat_rate). A label, not an FK:
an issued invoice is immutable, so a later catalogue edit must not change it.
Additive; existing lines keep NULL.

Revision ID: a99277d2853d
Revises: afc2c7fbc7ad
Create Date: 2026-07-21 13:16:34.348071
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a99277d2853d"
down_revision: Union[str, None] = "afc2c7fbc7ad"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("issued_invoice_lines", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tax_code", sa.String(length=24), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("issued_invoice_lines", schema=None) as batch_op:
        batch_op.drop_column("tax_code")
