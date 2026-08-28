"""WO-Z — the fuel-statement review queue: a finding that outlives its request.

One NEW TENANT table, with FORCE ROW LEVEL SECURITY in this same migration.

  vat_statement_findings

WHY
-----
`statement_ingest` has admitted since it was written that its returned
`warnings` list "IS the review surface until a persisted one exists". That list
lives for one HTTP response. Nothing enumerated what a statement had been
flagged for, so seeing a finding twice meant uploading the file again.

The refused case kept even less: when the capture gate blocks registration the
structured findings are folded into a message string and the transaction is
rolled back — so the one outcome where an operator most needs to know which
lines failed is the outcome that recorded nothing.

WHY THE UNIQUE INDEX IS PARTIAL
---------------------------------
`uq_vat_statement_findings_open` is unique over
(org_id, statement_sha256, fingerprint) WHERE status = 'open', where
`fingerprint` digests (code, line_seq, message) — the identity of the COMPLAINT
rather than of its source.

Re-uploading the same bytes must not pile up duplicates of one complaint. But a
finding that was resolved and then RECURS on a later parse has to come back:
because the resolved row is no longer `open`, it is no longer in the index, and
the recurrence gets its own row. A total unique constraint would have swallowed
it — the same defect `capture_failures.failure_seq` exists to prevent, where an
acknowledgement went on covering a fault that had happened again.

WHY THE KEY IS A FINGERPRINT AND NOT (code, line_seq)
-------------------------------------------------------
The obvious key is wrong in a way worth recording. Two post-capture checks can
flag different things about the same batch: same source code, and no line
number, because neither is about one line. Under (code, line_seq) the second
would have been refused as a duplicate of the first, and an operator would have
lost a real finding to an index. What makes two rows the same row is that they
say the same thing about the same statement.

Revision ID: a3c5e7f9b1d4
Revises: d2a4c6e8b0f3
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

import app.models.base
from alembic import op

revision: str = "a3c5e7f9b1d4"
down_revision: str | None = "d2a4c6e8b0f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("vat_statement_findings",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "vat_statement_findings",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("statement_sha256", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("network", sa.String(length=40), nullable=True),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("entity_id", app.models.base.GUID(), nullable=True),
        sa.Column("outcome", sa.String(length=12), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("line_seq", sa.Integer(), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=320), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
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
            "severity IN ('warn', 'error')", name="ck_vat_statement_findings_severity"
        ),
        sa.CheckConstraint(
            "outcome IN ('registered', 'refused')", name="ck_vat_statement_findings_outcome"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')",
            name="ck_vat_statement_findings_status",
        ),
        sa.CheckConstraint(
            "(status = 'open' AND resolved_at IS NULL AND resolved_by IS NULL) "
            "OR (status <> 'open' AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)",
            name="ck_vat_statement_findings_resolution_complete",
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_vat_statement_findings_org_id_id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_statement_findings_org_id"),
        "vat_statement_findings",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_vat_statement_findings_org_status",
        "vat_statement_findings",
        ["org_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_vat_statement_findings_org_sha",
        "vat_statement_findings",
        ["org_id", "statement_sha256"],
        unique=False,
    )
    op.create_index(
        "uq_vat_statement_findings_open",
        "vat_statement_findings",
        ["org_id", "statement_sha256", "fingerprint"],
        unique=True,
        sqlite_where=sa.text("status = 'open'"),
        postgresql_where=sa.text("status = 'open'"),
    )

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

    op.drop_index("uq_vat_statement_findings_open", table_name="vat_statement_findings")
    op.drop_index("ix_vat_statement_findings_org_sha", table_name="vat_statement_findings")
    op.drop_index("ix_vat_statement_findings_org_status", table_name="vat_statement_findings")
    op.drop_index(op.f("ix_vat_statement_findings_org_id"), table_name="vat_statement_findings")
    op.drop_table("vat_statement_findings")
