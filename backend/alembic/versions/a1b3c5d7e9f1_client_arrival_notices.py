"""WO-E client arrival notices: org schedule-notice settings, the project's
customer link, and the per-assignment notice override + sent stamp.

Columns only — no new tables, so no new RLS policies (projects,
organizations and project_assignments already carry theirs) and the docs
table-count tripwire stays at 98.

Revision ID: a1b3c5d7e9f1
Revises: f0a2b4c6d8e0
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision = "a1b3c5d7e9f1"
down_revision = "f0a2b4c6d8e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations", sa.Column("assignment_remind_hours", sa.Integer(), nullable=True)
    )
    op.add_column("organizations", sa.Column("client_notice_hours", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("customer_id", GUID(), nullable=True))
    op.add_column(
        "project_assignments",
        sa.Column("client_notice_hours_before", sa.Integer(), nullable=True),
    )
    op.add_column(
        "project_assignments",
        sa.Column("client_notice_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_assignments", "client_notice_sent_at")
    op.drop_column("project_assignments", "client_notice_hours_before")
    op.drop_column("projects", "customer_id")
    op.drop_column("organizations", "client_notice_hours")
    op.drop_column("organizations", "assignment_remind_hours")
