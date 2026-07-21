"""receipts + payment allocation (Slice 5c)

Adds the `receipts` table (money received — a bank transfer that may settle
several invoices) and `payments.receipt_id` (the receipt an allocation came
from). A receipt's allocations are exactly its `payments` rows, so the invoice
`amount_paid` cache stays the single source of truth. `payments.(org_id,
receipt_id)` is a COMPOSITE FK to `receipts(org_id, id)` — tenant-safe — so
receipts + its unique constraint are created BEFORE the FK.

Revision ID: 8c5ead96a8e4
Revises: e7da509694a5
Create Date: 2026-07-21 14:18:40.900866
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "8c5ead96a8e4"
down_revision: Union[str, None] = "e7da509694a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("receipts",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "receipts",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False, server_default="bank_transfer"),
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
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id", name="uq_receipts_org_id"),
    )
    op.create_index(op.f("ix_receipts_org_id"), "receipts", ["org_id"], unique=False)
    op.create_index("ix_receipts_org_received", "receipts", ["org_id", "received_on"], unique=False)

    with op.batch_alter_table("payments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("receipt_id", app.models.base.GUID(), nullable=True))
        batch_op.create_index("ix_payments_org_receipt", ["org_id", "receipt_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_payments_receipt",
            "receipts",
            ["org_id", "receipt_id"],
            ["org_id", "id"],
            ondelete="CASCADE",
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
    with op.batch_alter_table("payments", schema=None) as batch_op:
        batch_op.drop_constraint("fk_payments_receipt", type_="foreignkey")
        batch_op.drop_index("ix_payments_org_receipt")
        batch_op.drop_column("receipt_id")
    op.drop_index("ix_receipts_org_received", table_name="receipts")
    op.drop_index(op.f("ix_receipts_org_id"), table_name="receipts")
    op.drop_table("receipts")
