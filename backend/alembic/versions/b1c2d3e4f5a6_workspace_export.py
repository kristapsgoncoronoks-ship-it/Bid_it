"""PROD-009 — whole-workspace export requests, and a purge mark for export bytes

Revision ID: b1c2d3e4f5a6
Revises: a8b9c0d1e2f3
Create Date: 2026-09-09

Two things:

* `workspace_exports` — the request row for a live customer asking for
  everything their workspace holds. A tenant table, so registered, probed and
  ENABLE + FORCE RLS here, in this migration, like every other.
* `archive_exports.purged_at` — until now NOTHING ever destroyed a produced
  export zip. The `exports` document class only ever grew: the one-time link
  expires after seven days and the bytes stayed for ever. That was survivable
  while an export was one table; it is not once an export is the whole
  workspace, so `export.purge_expired` destroys the bytes of any export whose
  link has expired and stamps the row. The column exists on both request
  tables so the same purge can answer for both.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TENANT_TABLES = ("workspace_exports",)

_UNSET = (
    "current_setting('app.current_org', true) IS NULL "
    "OR current_setting('app.current_org', true) = ''"
)
_PREDICATE = f"{_UNSET} OR org_id::text = current_setting('app.current_org', true)"


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "workspace_exports",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id",
            GUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requested_email", sa.String(320), nullable=False),
        sa.Column("requested_by_user_id", GUID(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
        sa.Column("job_id", GUID(), nullable=True),
        sa.Column("download_token_id", GUID(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=True),
        sa.Column("tables", sa.Integer(), nullable=True),
        sa.Column("documents", sa.Integer(), nullable=True),
        sa.Column("missing_documents", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("link_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'ready', 'failed', 'downloaded')",
            name="ck_workspace_exports_status",
        ),
    )
    op.create_index("ix_workspace_exports_org_id", "workspace_exports", ["org_id"])

    op.add_column(
        "archive_exports", sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True)
    )

    if bind.dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            # The THREE-leg predicate of WO-27 / ADR-0028, not the two-leg one
            # most older migrations carry: `current_setting` returns `''`, never
            # SQL NULL, on any connection that has ever been scoped, and this
            # table's download route is deliberately unscoped.
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_column("archive_exports", "purged_at")
    op.drop_index("ix_workspace_exports_org_id", table_name="workspace_exports")
    op.drop_table("workspace_exports")
