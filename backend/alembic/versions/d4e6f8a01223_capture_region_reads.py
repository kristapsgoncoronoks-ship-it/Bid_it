"""Persist selected-region commands, results and deliberate application receipts.

IMPACT: one empty tenant table; existing extraction evidence is unchanged.
PREFLIGHT: stop capture writers/workers; apply c129fa806b32 first.
REFUSAL: normal source/composite tenant FK and command uniqueness constraints.
POSTCONDITION: immutable bounded OCR intent commits before processing and an
explicit application retains its original draft plus replay evidence.
ROLLBACK/REPAIR: retain the additive table for application rollback. Downgrade
refuses applied evidence; export/retain the command history before removal.
"""

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID

revision = "d4e6f8a01223"
down_revision = "c129fa806b32"
branch_labels = None
depends_on = None
TENANT_TABLES = ("capture_region_reads",)


def upgrade():
    op.create_table(
        "capture_region_reads",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id", GUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("extraction_run_id", GUID(), nullable=False),
        sa.Column(
            "requested_by", GUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("command_key", sa.String(36), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("attempt_seq", sa.Integer(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(48), nullable=True),
        sa.Column("error_detail", sa.String(500), nullable=True),
        sa.Column("processing_token", sa.String(36), nullable=True),
        sa.Column("processing_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("apply_command_key", sa.String(36), nullable=True),
        sa.Column("apply_payload_hash", sa.String(64), nullable=True),
        sa.Column("apply_result_json", sa.Text(), nullable=True),
        sa.Column("before_draft_json", sa.Text(), nullable=True),
        sa.Column(
            "applied_by", GUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "extraction_run_id"],
            ["extraction_runs.org_id", "extraction_runs.id"],
            ondelete="CASCADE",
            name="fk_capture_region_run",
        ),
        sa.UniqueConstraint(
            "org_id", "requested_by", "command_key", name="uq_capture_region_command"
        ),
    )
    op.create_index(
        "ix_capture_region_org_run",
        "capture_region_reads",
        ["org_id", "extraction_run_id", "created_at"],
    )
    op.create_index("ix_capture_region_org_status", "capture_region_reads", ["org_id", "status"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE capture_region_reads ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE capture_region_reads FORCE ROW LEVEL SECURITY")
        predicate = "(current_setting('app.current_org', true) IS NULL OR current_setting('app.current_org', true) = '' OR org_id::text = current_setting('app.current_org', true))"
        op.execute(
            f"CREATE POLICY tenant_isolation ON capture_region_reads USING {predicate} WITH CHECK {predicate}"
        )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM capture_region_reads WHERE applied_at IS NOT NULL LIMIT 1"))
        .scalar()
    ):
        raise RuntimeError("Applied region evidence exists; retain the additive table.")
    op.drop_table("capture_region_reads")
