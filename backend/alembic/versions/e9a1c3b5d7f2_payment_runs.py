"""supplier payment runs (Phase 14)

Adds `payment_runs` (a supplier payout batch, mirroring reimbursement_batches) and
a soft `invoices.payment_run_id` link (which run an invoice is queued in). Marking
a run paid writes AP payment-ledger entries per invoice; a paid run is a bank-
reconciliation debit target.

Revision ID: e9a1c3b5d7f2
Revises: d7f9b1e3a5c8
Create Date: 2026-07-23 06:45:00.000000
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "e9a1c3b5d7f2"
down_revision: Union[str, None] = "d7f9b1e3a5c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("payment_runs",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "payment_runs",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("reference", sa.String(length=200), nullable=True),
        sa.Column("method", sa.String(length=40), nullable=False, server_default="bank_transfer"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "total_eur", sa.Numeric(precision=14, scale=2), nullable=False, server_default="0"
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id", name="uq_payment_runs_org_id"),
    )
    op.create_index(op.f("ix_payment_runs_org_id"), "payment_runs", ["org_id"], unique=False)
    op.create_index(
        "ix_payment_runs_org_status", "payment_runs", ["org_id", "status"], unique=False
    )

    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.add_column(sa.Column("payment_run_id", app.models.base.GUID(), nullable=True))

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
    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.drop_column("payment_run_id")
    op.drop_index("ix_payment_runs_org_status", table_name="payment_runs")
    op.drop_index(op.f("ix_payment_runs_org_id"), table_name="payment_runs")
    op.drop_table("payment_runs")
