"""currency catalog — per-tenant currency registry (Slice 5a)

A tenant-scoped registry of the currencies a workspace transacts in (code, name,
symbol, minor units), for the currency pickers + display metadata. Additive,
tenant-scoped → Postgres RLS + the ORM guard. UNIQUE(org_id, id) is a future
composite-FK target.

Revision ID: c591d32e283a
Revises: a99277d2853d
Create Date: 2026-07-21 13:31:42.084086
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "c591d32e283a"
down_revision: Union[str, None] = "a99277d2853d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("currencies",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "currencies",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("code", sa.String(length=3), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("symbol", sa.String(length=8), nullable=True),
        sa.Column("decimal_places", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "code", name="uq_currencies_org_code"),
        sa.UniqueConstraint("org_id", "id", name="uq_currencies_org_id"),
    )
    op.create_index("ix_currencies_org_active", "currencies", ["org_id", "active"], unique=False)
    op.create_index(op.f("ix_currencies_org_id"), "currencies", ["org_id"], unique=False)

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
    op.drop_index(op.f("ix_currencies_org_id"), table_name="currencies")
    op.drop_index("ix_currencies_org_active", table_name="currencies")
    op.drop_table("currencies")
