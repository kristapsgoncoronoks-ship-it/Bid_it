"""object-storage references for receipts and logos

Adds sha256 + size columns so document bytes can live in object storage (ADR-0008)
instead of the database. The legacy `*_data` LargeBinary columns are kept as a
read fallback for pre-existing rows and dropped in a later contract migration.

Revision ID: a1b2c3d4e5f6
Revises: f708192a3b4c
Create Date: 2026-07-20 18:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f708192a3b4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('expense_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('receipt_sha256', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('receipt_size', sa.Integer(), nullable=True))

    with op.batch_alter_table('issuer_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('logo_sha256', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('logo_size', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('issuer_profiles', schema=None) as batch_op:
        batch_op.drop_column('logo_size')
        batch_op.drop_column('logo_sha256')

    with op.batch_alter_table('expense_items', schema=None) as batch_op:
        batch_op.drop_column('receipt_size')
        batch_op.drop_column('receipt_sha256')
