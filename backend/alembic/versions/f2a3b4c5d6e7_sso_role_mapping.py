"""sso_connections: IdP group -> role mapping (ADR-0021)

Adds groups_claim / role_mappings (JSON) / role_sync so a tenant's IdP groups can
drive InvoiceIQ roles on SSO login.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-07-22 00:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('sso_connections', schema=None) as batch_op:
        batch_op.add_column(sa.Column('groups_claim', sa.String(length=64), nullable=False, server_default='groups'))
        batch_op.add_column(sa.Column('role_mappings', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('role_sync', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('sso_connections', schema=None) as batch_op:
        batch_op.drop_column('role_sync')
        batch_op.drop_column('role_mappings')
        batch_op.drop_column('groups_claim')
