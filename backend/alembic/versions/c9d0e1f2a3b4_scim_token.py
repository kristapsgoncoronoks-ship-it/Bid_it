"""sso_connections: SCIM provisioning token (ADR-0021)

Adds scim_enabled + scim_token_hash so a tenant's IdP can authenticate to the
SCIM 2.0 endpoints with a bearer token (only its sha256 is stored).

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-21 21:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('sso_connections', schema=None) as batch_op:
        batch_op.add_column(sa.Column('scim_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('scim_token_hash', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_sso_connections_scim_token_hash', ['scim_token_hash'])


def downgrade() -> None:
    with op.batch_alter_table('sso_connections', schema=None) as batch_op:
        batch_op.drop_index('ix_sso_connections_scim_token_hash')
        batch_op.drop_column('scim_token_hash')
        batch_op.drop_column('scim_enabled')
