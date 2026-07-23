"""bank reconciliation — statements + lines (Phase 12)

Adds `bank_statements` (one imported statement) and `bank_lines` (its individual
transactions, reconciled to a receipt/reimbursement via a soft polymorphic
reference). `bank_lines.(org_id, statement_id)` is a COMPOSITE FK to
`bank_statements(org_id, id)` — tenant-safe — so the statement table + its unique
constraint are created BEFORE the FK.

Revision ID: c5e7a9b1d3f6
Revises: b3d5f7a9c1e4
Create Date: 2026-07-23 05:10:00.000000
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "c5e7a9b1d3f6"
down_revision: Union[str, None] = "b3d5f7a9c1e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("bank_statements", "bank_lines")

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def _ts_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "bank_statements",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("filename", sa.String(length=300), nullable=False),
        sa.Column("source_format", sa.String(length=12), nullable=False, server_default="csv"),
        sa.Column("method", sa.String(length=16), nullable=False, server_default="csv"),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(length=320), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id", name="uq_bank_statements_org_id"),
    )
    op.create_index(
        op.f("ix_bank_statements_org_id"), "bank_statements", ["org_id"], unique=False
    )

    op.create_table(
        "bank_lines",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("statement_id", app.models.base.GUID(), nullable=False),
        sa.Column("line_date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False, server_default="credit"),
        sa.Column("balance", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="unmatched"),
        sa.Column("matched_kind", sa.String(length=16), nullable=True),
        sa.Column("matched_id", app.models.base.GUID(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "statement_id"],
            ["bank_statements.org_id", "bank_statements.id"],
            name="fk_bank_lines_statement",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bank_lines_org_id"), "bank_lines", ["org_id"], unique=False)
    op.create_index(
        "ix_bank_lines_org_statement", "bank_lines", ["org_id", "statement_id"], unique=False
    )
    op.create_index("ix_bank_lines_org_status", "bank_lines", ["org_id", "status"], unique=False)

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
    op.drop_index("ix_bank_lines_org_status", table_name="bank_lines")
    op.drop_index("ix_bank_lines_org_statement", table_name="bank_lines")
    op.drop_index(op.f("ix_bank_lines_org_id"), table_name="bank_lines")
    op.drop_table("bank_lines")
    op.drop_index(op.f("ix_bank_statements_org_id"), table_name="bank_statements")
    op.drop_table("bank_statements")
