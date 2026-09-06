"""archive_exports — the ex-client archive export request (WO-AI)

Owner decision 2026-08-16 (§1.C): the archive survives a client who leaves
for the full retention period, and "an ex-client can request a one-time
EXPORT of their archive; no live login is retained". One row per request:
who asked (the owner address), what the worker produced, and whether the
one-time link was used. The link is an `email_tokens` row (hashed, single
use, expiring) — this table stores no secret.

NEW TENANT table — RLS lands in THIS SAME migration (master-context §4.2).

DOWNGRADE LOSES DATA: the request history. The produced zips live in object
storage under the `exports` document class and are not touched here.

Revision ID: b3c5d7e9f1a3
Revises: a2b4c6d8e0f2
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

import app.models.base  # portable GUID type used by every table
from alembic import op

revision: str = "b3c5d7e9f1a3"
down_revision: str | None = "a2b4c6d8e0f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("archive_exports",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "archive_exports",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("requested_email", sa.String(length=320), nullable=False),
        sa.Column("requested_by_user_id", app.models.base.GUID(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("job_id", app.models.base.GUID(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("records", sa.Integer(), nullable=True),
        sa.Column("missing_documents", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("link_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('queued', 'ready', 'failed', 'downloaded')",
            name="ck_archive_exports_status",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_archive_exports_org_id"), "archive_exports", ["org_id"], unique=False)

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

    op.drop_index(op.f("ix_archive_exports_org_id"), table_name="archive_exports")
    op.drop_table("archive_exports")
