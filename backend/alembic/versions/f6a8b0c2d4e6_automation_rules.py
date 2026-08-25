"""WO-J admin automation rules: rules + immutable versions + run log.

Revision ID: f6a8b0c2d4e6
Revises: e5f7a9b1c3d5
Create Date: 2026-08-25

Three tenant tables (docs/design/workflow-builder-research.md §3), each with
ENABLE+FORCE RLS here and registry + parity probes in the same commit.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision: str = "f6a8b0c2d4e6"
down_revision: Union[str, None] = "e5f7a9b1c3d5"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TENANT_TABLES = ("automation_rules", "automation_rule_versions", "automation_runs")


def _std_cols() -> list[sa.Column]:
    return [
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id",
            GUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
    ]


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "automation_rules",
        *_std_cols(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("trigger", sa.String(40), nullable=False),
        sa.Column("condition_json", sa.Text(), nullable=True),
        sa.Column("actions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("fire_policy", sa.String(16), nullable=False, server_default="once_per_record"),
        sa.Column("cooldown_hours", sa.Integer(), nullable=True),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(320), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("org_id", "id", name="uq_automation_rules_org_id"),
        sa.UniqueConstraint("org_id", "name", name="uq_automation_rules_org_name"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'disabled')", name="ck_automation_rules_status"
        ),
        sa.CheckConstraint(
            "fire_policy IN ('once_per_record', 'every_time', 'cooldown')",
            name="ck_automation_rules_fire_policy",
        ),
    )
    op.create_index("ix_automation_rules_org_id", "automation_rules", ["org_id"])
    op.create_index("ix_automation_rules_org_status", "automation_rules", ["org_id", "status"])

    op.create_table(
        "automation_rule_versions",
        *_std_cols(),
        sa.Column("rule_id", GUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("published_by", sa.String(320), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("org_id", "id", name="uq_automation_rule_versions_org_id"),
        sa.UniqueConstraint(
            "org_id", "rule_id", "version", name="uq_automation_rule_versions_rule_version"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "rule_id"],
            ["automation_rules.org_id", "automation_rules.id"],
            name="fk_automation_rule_versions_rule",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_automation_rule_versions_org_id", "automation_rule_versions", ["org_id"]
    )

    op.create_table(
        "automation_runs",
        *_std_cols(),
        sa.Column("rule_id", GUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("ref_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("org_id", "id", name="uq_automation_runs_org_id"),
        sa.CheckConstraint(
            "status IN ('ok', 'throttled', 'failed')", name="ck_automation_runs_status"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "rule_id"],
            ["automation_rules.org_id", "automation_rules.id"],
            name="fk_automation_runs_rule",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_automation_runs_org_id", "automation_runs", ["org_id"])
    op.create_index(
        "ix_automation_runs_org_rule_ref", "automation_runs", ["org_id", "rule_id", "ref_id"]
    )
    op.create_index(
        "ix_automation_runs_org_created", "automation_runs", ["org_id", "created_at"]
    )

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
    op.drop_index("ix_automation_runs_org_created", table_name="automation_runs")
    op.drop_index("ix_automation_runs_org_rule_ref", table_name="automation_runs")
    op.drop_index("ix_automation_runs_org_id", table_name="automation_runs")
    op.drop_table("automation_runs")
    op.drop_index("ix_automation_rule_versions_org_id", table_name="automation_rule_versions")
    op.drop_table("automation_rule_versions")
    op.drop_index("ix_automation_rules_org_status", table_name="automation_rules")
    op.drop_index("ix_automation_rules_org_id", table_name="automation_rules")
    op.drop_table("automation_rules")
