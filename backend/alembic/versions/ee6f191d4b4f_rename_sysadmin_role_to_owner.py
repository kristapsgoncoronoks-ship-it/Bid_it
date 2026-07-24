"""rename sysadmin role to owner

Revision ID: ee6f191d4b4f
Revises: 2b340c7660c5
Create Date: 2026-07-20 14:04:37.767423
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.models.base  # portable GUID type used by every table


revision: str = 'ee6f191d4b4f'
down_revision: Union[str, None] = '2b340c7660c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The company's top role is renamed 'sysadmin' → 'owner' (a company owner, not
    # a system administrator). Role is stored as a portable VARCHAR, so this is a
    # pure data update across the two tables that persist a role string.
    op.execute("UPDATE users SET role = 'owner' WHERE role = 'sysadmin'")
    op.execute("UPDATE role_policies SET role = 'owner' WHERE role = 'sysadmin'")


def downgrade() -> None:
    op.execute("UPDATE users SET role = 'sysadmin' WHERE role = 'owner'")
    op.execute("UPDATE role_policies SET role = 'sysadmin' WHERE role = 'owner'")
