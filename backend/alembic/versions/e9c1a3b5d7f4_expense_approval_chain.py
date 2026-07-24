"""expense multi-step approval chain: policies + per-report steps

Two new tenant-scoped tables (RLS + TENANT_TABLES). Mirrors the invoice approval
engine (approval_policies / approval_steps) for expense reports.

Revision ID: e9c1a3b5d7f4
Revises: d7f9b1e3a5c9
Create Date: 2026-07-23 13:05:00.000000
"""

from typing import Sequence, Union

import app.models.base
import sqlalchemy as sa
from alembic import op

revision: str = "e9c1a3b5d7f4"
down_revision: Union[str, None] = "d7f9b1e3a5c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("expense_approval_policies", "expense_approval_steps")

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)

_TS = dict(server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False)


def upgrade() -> None:
    # Widen expense_reports.status (16 → 32) so the longer workflow states
    # ("marked_for_reimbursement" = 24, "partially_approved" = 18) fit on Postgres,
    # where VARCHAR length is enforced (SQLite ignores it).
    with op.batch_alter_table("expense_reports", schema=None) as b:
        b.alter_column("status", type_=sa.String(length=32), existing_nullable=False)

    op.create_table(
        "expense_approval_policies",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("min_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("approver_ids", sa.Text(), nullable=True),
        sa.Column("finance_final", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("finance_approver_id", app.models.base.GUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("id", app.models.base.GUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id", name="uq_expense_approval_policies_org_id"),
    )
    op.create_index(
        op.f("ix_expense_approval_policies_org_id"),
        "expense_approval_policies",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_expense_approval_policies_org_active",
        "expense_approval_policies",
        ["org_id", "active", "priority"],
        unique=False,
    )

    op.create_table(
        "expense_approval_steps",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("report_id", app.models.base.GUID(), nullable=False),
        sa.Column("policy_id", app.models.base.GUID(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="approver"),
        sa.Column("approver_id", app.models.base.GUID(), nullable=True),
        sa.Column("approver_email", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("decided_by", app.models.base.GUID(), nullable=True),
        sa.Column("decided_by_email", sa.String(length=200), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("id", app.models.base.GUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["report_id"], ["expense_reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "report_id", "seq", name="uq_expense_approval_steps_seq"),
    )
    op.create_index(
        op.f("ix_expense_approval_steps_org_id"),
        "expense_approval_steps",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_expense_approval_steps_report_id"),
        "expense_approval_steps",
        ["report_id"],
        unique=False,
    )
    op.create_index(
        "ix_expense_approval_steps_report",
        "expense_approval_steps",
        ["org_id", "report_id"],
        unique=False,
    )
    op.create_index(
        "ix_expense_approval_steps_inbox",
        "expense_approval_steps",
        ["org_id", "approver_id", "status"],
        unique=False,
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
    op.drop_table("expense_approval_steps")
    op.drop_table("expense_approval_policies")
    with op.batch_alter_table("expense_reports", schema=None) as b:
        b.alter_column("status", type_=sa.String(length=16), existing_nullable=False)
