"""drop legacy in-DB document blob columns (ADR-0008 contract migration)

Document bytes have lived in object storage (with dual-read fallback) since the
object-storage migration. This closes that window: the legacy LargeBinary
columns are removed now that reads go through object storage only.

Deployment note: run only AFTER every legacy blob has been backfilled into object
storage (a row with bytes ONLY in `*_data` and no sha256 would lose its file).
Fresh installs and the dev/test DBs have no such rows.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-21 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('expense_items', schema=None) as batch_op:
        batch_op.drop_column('receipt_data')
    with op.batch_alter_table('issuer_profiles', schema=None) as batch_op:
        batch_op.drop_column('logo_data')
    with op.batch_alter_table('inbound_invoices', schema=None) as batch_op:
        batch_op.drop_column('file_data')


def downgrade() -> None:
    with op.batch_alter_table('inbound_invoices', schema=None) as batch_op:
        batch_op.add_column(sa.Column('file_data', sa.LargeBinary(), nullable=True))
    with op.batch_alter_table('issuer_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('logo_data', sa.LargeBinary(), nullable=True))
    with op.batch_alter_table('expense_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('receipt_data', sa.LargeBinary(), nullable=True))
