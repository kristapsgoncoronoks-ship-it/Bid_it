"""organizations.region — data residency / region-pinning (ADR-0022)

The region a tenant's data is pinned to. Existing rows default to the current
`service_region` ('eu'); assigned at registration going forward.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-07-21 23:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('region', sa.String(length=20), nullable=False, server_default='eu'))


def downgrade() -> None:
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_column('region')
