"""payment ledger — extract issued_invoices.amount_paid into a history (Slice 3c)

Adds the `payments` ledger (the history behind the AR running total) and backfills
one 'migrated' entry per already-settled issued invoice so SUM(payments.amount)
equals the existing `amount_paid` cache from day one.

`payments.(org_id, issued_invoice_id)` is a COMPOSITE FK to
`issued_invoices(org_id, id)` — tenant-safe by construction — so the unique
constraint on the parent is created FIRST (Postgres requires it before the FK).

Revision ID: d99c826e4767
Revises: de3b47386d45
Create Date: 2026-07-21 12:43:31.039011
"""

import uuid
from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "d99c826e4767"
down_revision: Union[str, None] = "de3b47386d45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("payments",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    # 1) The composite-FK target must be unique BEFORE the FK references it.
    with op.batch_alter_table("issued_invoices", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_issued_invoices_org_id", ["org_id", "id"])

    # 2) The ledger table.
    op.create_table(
        "payments",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("issued_invoice_id", app.models.base.GUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False),
        sa.Column("reference", sa.String(length=140), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["org_id", "issued_invoice_id"],
            ["issued_invoices.org_id", "issued_invoices.id"],
            name="fk_payments_issued_invoice",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_payments_org_id"), "payments", ["org_id"], unique=False)
    op.create_index(
        "ix_payments_org_invoice", "payments", ["org_id", "issued_invoice_id"], unique=False
    )

    # 3) Backfill: one 'migrated' entry per already-settled invoice, so the ledger
    #    SUM matches the existing amount_paid cache. paid_on falls back to the
    #    issue date when the invoice was never stamped with a payment date.
    issued = sa.table(
        "issued_invoices",
        sa.column("id"),
        sa.column("org_id"),
        sa.column("amount_paid"),
        sa.column("paid_date"),
        sa.column("issue_date"),
    )
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(
            issued.c.id,
            issued.c.org_id,
            issued.c.amount_paid,
            issued.c.paid_date,
            issued.c.issue_date,
        ).where(issued.c.amount_paid != 0)
    ).fetchall()
    if rows:
        payments = sa.table(
            "payments",
            sa.column("id"),
            sa.column("org_id"),
            sa.column("issued_invoice_id"),
            sa.column("amount"),
            sa.column("paid_on"),
            sa.column("method"),
        )
        op.bulk_insert(
            payments,
            [
                {
                    "id": str(uuid.uuid4()),
                    "org_id": r.org_id,
                    "issued_invoice_id": r.id,
                    "amount": r.amount_paid,
                    "paid_on": r.paid_date or r.issue_date,
                    "method": "migrated",
                }
                for r in rows
            ],
        )

    # 4) Postgres RLS.
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
    op.drop_index("ix_payments_org_invoice", table_name="payments")
    op.drop_index(op.f("ix_payments_org_id"), table_name="payments")
    op.drop_table("payments")
    with op.batch_alter_table("issued_invoices", schema=None) as batch_op:
        batch_op.drop_constraint("uq_issued_invoices_org_id", type_="unique")
