"""Durable per-file capture admission commands, retained after capture purge.

IMPACT: additive command table and nullable run link; legacy captures unchanged.
PREFLIGHT: quiesce capture writers; old writers do not accept command identities.
POSTCONDITION: org/key and org/command-run uniqueness; composite tenant FK; RLS.
ROLLBACK: retain schema for application rollback. Downgrade refuses committed
commands, including retired ones; deleting their identity could replay admission.
"""

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID

revision = "238cd0be715f"
down_revision = "127bc9ad604e"
branch_labels = None
depends_on = None

TENANT_TABLES = ["capture_commands"]


def upgrade():
    op.create_table(
        "capture_commands",
        sa.Column("id", GUID(), nullable=False),
        sa.Column(
            "org_id", GUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("command_key", sa.String(120), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id", name="uq_capture_commands_org_id"),
        sa.UniqueConstraint("org_id", "command_key", name="uq_capture_commands_key"),
    )
    op.create_index("ix_capture_commands_org_id", "capture_commands", ["org_id"])
    with op.batch_alter_table("extraction_runs") as batch:
        batch.add_column(sa.Column("admission_command_id", GUID(), nullable=True))
        batch.create_unique_constraint(
            "uq_extraction_runs_command", ["org_id", "admission_command_id"]
        )
        batch.create_foreign_key(
            "fk_extraction_runs_command",
            "capture_commands",
            ["org_id", "admission_command_id"],
            ["org_id", "id"],
            ondelete="CASCADE",
        )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE capture_commands ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE capture_commands FORCE ROW LEVEL SECURITY")
        predicate = "(current_setting('app.current_org', true) IS NULL OR current_setting('app.current_org', true) = '' OR org_id::text = current_setting('app.current_org', true))"
        op.execute(
            f"CREATE POLICY tenant_isolation ON capture_commands USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM capture_commands")):
        raise RuntimeError("Retain committed capture commands; do not discard replay identity.")
    with op.batch_alter_table("extraction_runs") as batch:
        batch.drop_constraint("fk_extraction_runs_command", type_="foreignkey")
        batch.drop_constraint("uq_extraction_runs_command", type_="unique")
        batch.drop_column("admission_command_id")
    op.drop_table("capture_commands")
