"""tax-code catalog — per-tenant VAT/tax-code registry (Slice 4a)

A tenant-scoped catalogue of named VAT rates + categories the issuing UI picks
from (and a later slice links from invoice lines). Additive, tenant-scoped →
Postgres RLS + the ORM guard. UNIQUE(org_id, id) is the future composite-FK
target for that line link.

Revision ID: afc2c7fbc7ad
Revises: d99c826e4767
Create Date: 2026-07-21 13:02:29.980202
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "afc2c7fbc7ad"
down_revision: Union[str, None] = "d99c826e4767"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("tax_codes",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "tax_codes",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("rate", sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False, server_default="standard"),
        sa.Column("country", sa.String(length=2), nullable=True),
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
        sa.UniqueConstraint("org_id", "code", name="uq_tax_codes_org_code"),
        sa.UniqueConstraint("org_id", "id", name="uq_tax_codes_org_id"),
    )
    op.create_index("ix_tax_codes_org_active", "tax_codes", ["org_id", "active"], unique=False)
    op.create_index("ix_tax_codes_org_country", "tax_codes", ["org_id", "country"], unique=False)
    op.create_index(op.f("ix_tax_codes_org_id"), "tax_codes", ["org_id"], unique=False)

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
    op.drop_index(op.f("ix_tax_codes_org_id"), table_name="tax_codes")
    op.drop_index("ix_tax_codes_org_country", table_name="tax_codes")
    op.drop_index("ix_tax_codes_org_active", table_name="tax_codes")
    op.drop_table("tax_codes")
