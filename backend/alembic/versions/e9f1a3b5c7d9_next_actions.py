"""next-actions support tables: deadline templates + dismissals

Revision ID: e9f1a3b5c7d9
Revises: d8e0f2a4b6c8
Create Date: 2026-08-23

WO-C (docs/design/tasks-module-research.md). Two tenant tables, both with
ENABLE+FORCE RLS here and registry + parity probes in the same commit. The
action items themselves are DERIVED — no task table exists by design.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision: str = "e9f1a3b5c7d9"
down_revision: Union[str, None] = "d8e0f2a4b6c8"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TENANT_TABLES = ("org_deadlines", "action_dismissals")


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "org_deadlines",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id",
            GUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("cadence", sa.String(12), nullable=False, server_default="monthly"),
        sa.Column("due_day", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("lead_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("last_done_period", sa.String(10), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_org_deadlines_org_id_id"),
        sa.CheckConstraint(
            "cadence IN ('monthly', 'quarterly', 'yearly')", name="ck_org_deadlines_cadence"
        ),
        sa.CheckConstraint("due_day >= 1 AND due_day <= 28", name="ck_org_deadlines_due_day"),
    )
    op.create_index("ix_org_deadlines_org_id", "org_deadlines", ["org_id"])

    op.create_table(
        "action_dismissals",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id",
            GUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("ref_id", sa.String(64), nullable=False),
        sa.Column("dismissed_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("org_id", "kind", "ref_id", name="uq_action_dismissals_org_kind_ref"),
    )
    op.create_index("ix_action_dismissals_org_id", "action_dismissals", ["org_id"])

    if bind.dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                f"USING (current_setting('app.current_org', true) IS NULL OR org_id::text = current_setting('app.current_org', true)) WITH CHECK (current_setting('app.current_org', true) IS NULL OR org_id::text = current_setting('app.current_org', true))"
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_index("ix_action_dismissals_org_id", table_name="action_dismissals")
    op.drop_table("action_dismissals")
    op.drop_index("ix_org_deadlines_org_id", table_name="org_deadlines")
    op.drop_table("org_deadlines")
