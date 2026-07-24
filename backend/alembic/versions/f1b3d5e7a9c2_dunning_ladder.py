"""dunning ladder (Phase 16)

Adds `dunning_policies` (a per-tenant escalation ladder: level → days_overdue
threshold + tone) and `issued_invoices.dunning_level` (the highest level already
fired for an invoice, so each level sends at most once). Absence of policy rows =
the built-in default ladder.

Revision ID: f1b3d5e7a9c2
Revises: e9a1c3b5d7f2
Create Date: 2026-07-23 07:30:00.000000
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "f1b3d5e7a9c2"
down_revision: Union[str, None] = "e9a1c3b5d7f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("dunning_policies",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "dunning_policies",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("days_overdue", sa.Integer(), nullable=False),
        sa.Column("tone", sa.String(length=16), nullable=False, server_default="reminder"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
        sa.UniqueConstraint("org_id", "level", name="uq_dunning_org_level"),
        sa.UniqueConstraint("org_id", "id", name="uq_dunning_org_id"),
    )
    op.create_index(
        op.f("ix_dunning_policies_org_id"), "dunning_policies", ["org_id"], unique=False
    )

    with op.batch_alter_table("issued_invoices", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("dunning_level", sa.Integer(), nullable=False, server_default="0")
        )

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
    with op.batch_alter_table("issued_invoices", schema=None) as batch_op:
        batch_op.drop_column("dunning_level")
    op.drop_index(op.f("ix_dunning_policies_org_id"), table_name="dunning_policies")
    op.drop_table("dunning_policies")
