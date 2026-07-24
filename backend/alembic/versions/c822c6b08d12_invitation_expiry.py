"""invitation expiry (Slice 5)

Adds `invitations.expires_at` (nullable — existing open invites keep NULL = never
expires; new invites are stamped +14d). Additive, no backfill.

Revision ID: c822c6b08d12
Revises: 9402daee00cb
Create Date: 2026-07-22 05:37:39.356866
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c822c6b08d12"
down_revision: Union[str, None] = "9402daee00cb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("invitations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("invitations", schema=None) as batch_op:
        batch_op.drop_column("expires_at")
