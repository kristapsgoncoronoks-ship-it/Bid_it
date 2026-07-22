"""email verification + password reset (Slice 3)

Adds `users.email_verified` (existing accounts backfilled TRUE via server_default,
so enabling the verification gate later never locks them out; new accounts are
set FALSE by the app at registration) and the `auth_tokens` table — single-use,
hashed email-verification / password-reset tokens. Tenant-scoped → RLS.

Revision ID: 0739a824262d
Revises: 407cf4fff58b
Create Date: 2026-07-22 04:08:17.619334
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "0739a824262d"
down_revision: Union[str, None] = "407cf4fff58b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("auth_tokens",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    # Backfill existing users as verified (server_default); the app sets FALSE for
    # new registrations explicitly.
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "email_verified", sa.Boolean(), nullable=False, server_default=sa.true()
            )
        )

    op.create_table(
        "auth_tokens",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("user_id", app.models.base.GUID(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", app.models.base.GUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_auth_tokens_org_id"), "auth_tokens", ["org_id"], unique=False)
    op.create_index("ix_auth_tokens_org_user", "auth_tokens", ["org_id", "user_id"], unique=False)
    op.create_index(
        op.f("ix_auth_tokens_token_hash"), "auth_tokens", ["token_hash"], unique=True
    )
    op.create_index(op.f("ix_auth_tokens_user_id"), "auth_tokens", ["user_id"], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_index(op.f("ix_auth_tokens_user_id"), table_name="auth_tokens")
    op.drop_index(op.f("ix_auth_tokens_token_hash"), table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_org_user", table_name="auth_tokens")
    op.drop_index(op.f("ix_auth_tokens_org_id"), table_name="auth_tokens")
    op.drop_table("auth_tokens")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("email_verified")
