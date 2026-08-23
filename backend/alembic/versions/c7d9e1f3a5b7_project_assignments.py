"""work-planning assignments — a person on a project for a window

Revision ID: c7d9e1f3a5b7
Revises: b6c8d0e2f4a6
Create Date: 2026-08-23

Phase A of docs/design/work-calendar.md (WO-A). One tenant table; three
isolation layers land together: org_id column here, ENABLE+FORCE RLS here,
TENANT_MODELS registration + parity probe in the same commit.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision: str = "c7d9e1f3a5b7"
down_revision: Union[str, None] = "b6c8d0e2f4a6"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TENANT_TABLES = ("project_assignments",)


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "project_assignments",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id",
            GUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", GUID(), nullable=False),
        sa.Column("assignee_user_id", GUID(), nullable=False),
        sa.Column("assignee_email", sa.String(255), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("all_day", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_project_assignments_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_project_assignments_org_id_id"),
        sa.CheckConstraint(
            "status IN ('planned', 'confirmed', 'done', 'cancelled')",
            name="ck_project_assignments_status",
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_project_assignments_window"),
    )
    op.create_index(
        "ix_project_assignments_org_assignee_start",
        "project_assignments",
        ["org_id", "assignee_user_id", "starts_at"],
    )
    op.create_index(
        "ix_project_assignments_org_start", "project_assignments", ["org_id", "starts_at"]
    )
    op.create_index(
        "ix_project_assignments_org_project", "project_assignments", ["org_id", "project_id"]
    )
    op.create_index("ix_project_assignments_org_id", "project_assignments", ["org_id"])

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
    for ix in (
        "ix_project_assignments_org_id",
        "ix_project_assignments_org_project",
        "ix_project_assignments_org_start",
        "ix_project_assignments_org_assignee_start",
    ):
        op.drop_index(ix, table_name="project_assignments")
    op.drop_table("project_assignments")
