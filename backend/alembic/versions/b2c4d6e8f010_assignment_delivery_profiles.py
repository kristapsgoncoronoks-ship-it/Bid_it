"""Persist assignment time zones, revision fences and email preferences.

IMPACT: additive assignment columns; no existing timestamps or sent markers move.
PREFLIGHT: stop scheduling writers and assignment email workers for migration.
NORMALIZATION: legacy rows retain UTC interpretation and existing notification
defaults; historical browser-local time zones are not guessed.
POSTCONDITION: current writers can preserve opt-outs and reject stale changes.
ROLLBACK/REPAIR: retain additive schema for application rollback. Old writers
ignore opt-outs/revisions and must stay disabled. Downgrade refuses non-default
preferences or changed revisions instead of destroying their evidence.
"""

import sqlalchemy as sa

from alembic import op

revision = "b2c4d6e8f010"
down_revision = "c129fa806b32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("project_assignments") as batch:
        batch.add_column(
            sa.Column("time_zone", sa.String(64), nullable=False, server_default="UTC")
        )
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(
            sa.Column("notify_assignee", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.add_column(
            sa.Column("reminders_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.add_column(sa.Column("client_notice_enabled", sa.Boolean(), nullable=True))
        batch.create_check_constraint("ck_project_assignments_revision", "revision >= 1")


def downgrade() -> None:
    changed = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM project_assignments WHERE time_zone <> 'UTC' OR revision <> 1 "
                "OR notify_assignee = false OR reminders_enabled = false "
                "OR client_notice_enabled IS NOT NULL LIMIT 1"
            )
        )
        .scalar()
    )
    if changed:
        raise RuntimeError("Assignment preferences/revisions exist; retain the additive schema.")
    with op.batch_alter_table("project_assignments") as batch:
        batch.drop_constraint("ck_project_assignments_revision", type_="check")
        for name in (
            "client_notice_enabled",
            "reminders_enabled",
            "notify_assignee",
            "revision",
            "time_zone",
        ):
            batch.drop_column(name)
