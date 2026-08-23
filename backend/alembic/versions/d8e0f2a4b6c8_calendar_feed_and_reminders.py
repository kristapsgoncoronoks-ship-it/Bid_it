"""calendar feed tokens + assignment reminder columns

Revision ID: d8e0f2a4b6c8
Revises: c7d9e1f3a5b7
Create Date: 2026-08-23

WO-B (docs/design/work-calendar.md phases B + B2): `calendar_feed_tokens`
(tenant table — org_id + RLS here, registry + probe in the same commit) and
two columns on `project_assignments`: `remind_hours_before` (nullable
per-assignment override; the code default is 24) and `reminder_sent_at`
(the idempotency stamp — the queue is at-least-once, one reminder is the
contract).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision: str = "d8e0f2a4b6c8"
down_revision: Union[str, None] = "c7d9e1f3a5b7"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TENANT_TABLES = ("calendar_feed_tokens",)


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "calendar_feed_tokens",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id",
            GUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("org_id", "user_id", name="uq_calendar_feed_tokens_org_user"),
        sa.UniqueConstraint("token", name="uq_calendar_feed_tokens_token"),
    )
    op.create_index("ix_calendar_feed_tokens_org_id", "calendar_feed_tokens", ["org_id"])

    op.add_column(
        "project_assignments", sa.Column("remind_hours_before", sa.Integer(), nullable=True)
    )
    op.add_column(
        "project_assignments",
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
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
    op.drop_column("project_assignments", "reminder_sent_at")
    op.drop_column("project_assignments", "remind_hours_before")
    op.drop_index("ix_calendar_feed_tokens_org_id", table_name="calendar_feed_tokens")
    op.drop_table("calendar_feed_tokens")
