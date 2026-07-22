"""membership identity snapshot (Slice 6e)

Denormalises email + name onto memberships so the member roster reads ONLY
memberships (org-scoped + RLS-safe, and complete — includes members currently
active in another org) without de-scoping the User table. Backfills from users.

Revision ID: 955bd43a8cb3
Revises: deb447b02296
Create Date: 2026-07-22 07:42:59.913992
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "955bd43a8cb3"
down_revision: Union[str, None] = "deb447b02296"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("memberships", schema=None) as batch_op:
        batch_op.add_column(sa.Column("email", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("name", sa.String(length=200), nullable=True))

    # Backfill the snapshot from the owning user.
    bind = op.get_bind()
    for user_id, email, name in bind.execute(sa.text("SELECT id, email, name FROM users")):
        bind.execute(
            sa.text("UPDATE memberships SET email = :e, name = :n WHERE user_id = :u"),
            {"e": email, "n": name, "u": user_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("memberships", schema=None) as batch_op:
        batch_op.drop_column("name")
        batch_op.drop_column("email")
