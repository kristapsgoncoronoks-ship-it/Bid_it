"""users: brute-force login lockout counters

Adds failed_login_count + locked_until so the login handler can lock an account
after N consecutive failed attempts. Additive columns on an existing table — no
RLS/TENANT_TABLES change (users is already tenant-scoped).

Revision ID: c5e7a9b1d3f7
Revises: b4d6f8a0c2e4
Create Date: 2026-07-23 12:05:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5e7a9b1d3f7"
down_revision: Union[str, None] = "b4d6f8a0c2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as b:
        b.add_column(
            sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0")
        )
        b.add_column(sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as b:
        b.drop_column("locked_until")
        b.drop_column("failed_login_count")
