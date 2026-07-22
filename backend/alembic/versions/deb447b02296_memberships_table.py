"""memberships table + backfill (Slice 6a)

The tenant relationship between a user and an org, split out of the user row so a
person can belong to several orgs. This slice only ADDS the table and backfills
one membership per existing user; readers still use `users.org_id`/`users.role`.
Tenant-scoped → RLS.

Revision ID: deb447b02296
Revises: c822c6b08d12
Create Date: 2026-07-22 05:52:45.042069
"""

import uuid
from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "deb447b02296"
down_revision: Union[str, None] = "c822c6b08d12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("memberships",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "memberships",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("user_id", app.models.base.GUID(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("user_free", "user", "admin", "owner", name="user_role", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("is_expense_approver", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
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
        sa.UniqueConstraint("user_id", "org_id", name="uq_memberships_user_org"),
    )
    op.create_index(op.f("ix_memberships_org_id"), "memberships", ["org_id"], unique=False)
    op.create_index("ix_memberships_org_user", "memberships", ["org_id", "user_id"], unique=False)
    op.create_index(op.f("ix_memberships_user_id"), "memberships", ["user_id"], unique=False)

    _backfill(op.get_bind())

    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
            )


def _backfill(bind) -> None:
    """One membership per existing user (role/approver copied; status from is_active)."""
    m = sa.table(
        "memberships",
        sa.column("id", app.models.base.GUID()),
        sa.column("org_id", app.models.base.GUID()),
        sa.column("user_id", app.models.base.GUID()),
        sa.column("role", sa.String()),
        sa.column("is_expense_approver", sa.Boolean()),
        sa.column("status", sa.String()),
    )
    rows = []
    for org_id, user_id, role, approver, is_active in bind.execute(
        sa.text("SELECT org_id, id, role, is_expense_approver, is_active FROM users")
    ):
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "org_id": org_id,
                "user_id": user_id,
                "role": role,
                "is_expense_approver": bool(approver),
                "status": "active" if is_active else "suspended",
            }
        )
    if rows:
        op.bulk_insert(m, rows)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_index(op.f("ix_memberships_user_id"), table_name="memberships")
    op.drop_index("ix_memberships_org_user", table_name="memberships")
    op.drop_index(op.f("ix_memberships_org_id"), table_name="memberships")
    op.drop_table("memberships")
