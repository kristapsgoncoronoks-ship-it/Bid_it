"""issued invoices: per-line discount, PO reference, exemption reason + attachments

Adds a per-line discount_percent, invoice-level po_reference and
tax_exemption_reason, and a tenant-scoped issued_invoice_attachments table for
supporting documents (signed PO, delivery note, contract).

Revision ID: c7e9f1b3d5a8
Revises: b5d7f9a1c3e6
Create Date: 2026-07-23 16:05:00.000000
"""

from typing import Sequence, Union

import app.models.base
import sqlalchemy as sa
from alembic import op

revision: str = "c7e9f1b3d5a8"
down_revision: Union[str, None] = "b5d7f9a1c3e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("issued_invoice_attachments",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)
_TS = dict(server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False)
_G = app.models.base.GUID


def upgrade() -> None:
    with op.batch_alter_table("issued_invoices", schema=None) as b:
        b.add_column(sa.Column("po_reference", sa.String(length=60), nullable=True))
        b.add_column(sa.Column("tax_exemption_reason", sa.String(length=300), nullable=True))
    with op.batch_alter_table("issued_invoice_lines", schema=None) as b:
        b.add_column(
            sa.Column(
                "discount_percent",
                sa.Numeric(precision=5, scale=2),
                nullable=False,
                server_default="0",
            )
        )

    op.create_table(
        "issued_invoice_attachments",
        sa.Column("org_id", _G(), nullable=False),
        sa.Column("invoice_id", _G(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime", sa.String(length=120), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("uploaded_by", _G(), nullable=True),
        sa.Column("uploaded_by_email", sa.String(length=200), nullable=True),
        sa.Column("id", _G(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "invoice_id"],
            ["issued_invoices.org_id", "issued_invoices.id"],
            name="fk_issued_attachments_invoice",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id", name="uq_issued_attachments_org_id"),
    )
    op.create_index(
        op.f("ix_issued_invoice_attachments_org_id"),
        "issued_invoice_attachments",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_issued_attachments_invoice",
        "issued_invoice_attachments",
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
    op.drop_index("ix_issued_attachments_invoice", table_name="issued_invoice_attachments")
    op.drop_index(
        op.f("ix_issued_invoice_attachments_org_id"), table_name="issued_invoice_attachments"
    )
    op.drop_table("issued_invoice_attachments")
    with op.batch_alter_table("issued_invoice_lines", schema=None) as b:
        b.drop_column("discount_percent")
    with op.batch_alter_table("issued_invoices", schema=None) as b:
        b.drop_column("tax_exemption_reason")
        b.drop_column("po_reference")
