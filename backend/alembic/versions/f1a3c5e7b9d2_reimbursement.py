"""expense reimbursement — payment metadata + payout batches (Phase 09)

Adds real reimbursement to the expense vertical:
- expense_reports: reimbursed_at, payment_method, payment_reference, payout_batch_id;
- reimbursement_batches: a payout run grouping approved reports for payment,
  marked paid together and exportable as a bank/payroll file (tenant-scoped → RLS).

Revision ID: f1a3c5e7b9d2
Revises: e7b9c1d3f5a2
Create Date: 2026-07-22 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.models.base


revision: str = 'f1a3c5e7b9d2'
down_revision: Union[str, None] = 'e7b9c1d3f5a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("reimbursement_batches",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)

_TS = dict(server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False)


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"

    # --- expense_reports: reimbursement columns -----------------------------
    op.add_column(
        "expense_reports", sa.Column("reimbursed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("expense_reports", sa.Column("payment_method", sa.String(length=40), nullable=True))
    op.add_column(
        "expense_reports", sa.Column("payment_reference", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "expense_reports", sa.Column("payout_batch_id", app.models.base.GUID(), nullable=True)
    )
    op.create_index(
        "ix_expense_reports_payout_batch_id", "expense_reports", ["payout_batch_id"]
    )

    # --- reimbursement_batches ----------------------------------------------
    op.create_table(
        "reimbursement_batches",
        sa.Column("id", app.models.base.GUID(), nullable=False),
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("reference", sa.String(length=200), nullable=True),
        sa.Column("method", sa.String(length=40), nullable=False, server_default="bank_transfer"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("total_eur", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id", name="uq_reimbursement_batches_org_id"),
    )
    op.create_index("ix_reimbursement_batches_org_id", "reimbursement_batches", ["org_id"])
    op.create_index(
        "ix_reimbursement_batches_org_status", "reimbursement_batches", ["org_id", "status"]
    )

    if is_pg:
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
            )


def downgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    if is_pg:
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_table("reimbursement_batches")
    op.drop_index("ix_expense_reports_payout_batch_id", table_name="expense_reports")
    for col in ("payout_batch_id", "payment_reference", "payment_method", "reimbursed_at"):
        op.drop_column("expense_reports", col)
