"""supplier payment ledger (Phase 13) — AP settlement

Adds the AP mirror of the AR payment ledger: `invoices.amount_paid`/`paid_date`
(the derived running-total cache) and a `supplier_payments` table (signed
settlement entries; the ledger is the source of truth). `supplier_payments.
(org_id, invoice_id)` is a COMPOSITE FK to `invoices(org_id, id)` — tenant-safe
(the `uq_invoices_org_id` unique constraint already exists).

Revision ID: d7f9b1e3a5c8
Revises: c5e7a9b1d3f6
Create Date: 2026-07-23 06:20:00.000000
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "d7f9b1e3a5c8"
down_revision: Union[str, None] = "c5e7a9b1d3f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("supplier_payments",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "amount_paid",
                sa.Numeric(precision=14, scale=2),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(sa.Column("paid_date", sa.Date(), nullable=True))

    op.create_table(
        "supplier_payments",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("invoice_id", app.models.base.GUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False, server_default="bank_transfer"),
        sa.Column("reference", sa.String(length=140), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("run_id", app.models.base.GUID(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["org_id", "invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_supplier_payments_invoice",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_supplier_payments_org_id"), "supplier_payments", ["org_id"], unique=False
    )
    op.create_index(
        "ix_supplier_payments_org_invoice",
        "supplier_payments",
        ["org_id", "invoice_id"],
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
    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_index("ix_supplier_payments_org_invoice", table_name="supplier_payments")
    op.drop_index(op.f("ix_supplier_payments_org_id"), table_name="supplier_payments")
    op.drop_table("supplier_payments")
    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.drop_column("paid_date")
        batch_op.drop_column("amount_paid")
