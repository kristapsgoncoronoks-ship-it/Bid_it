"""Capture supplier document identifiers (N1, EN-16931 BT-30 / BT-31).

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8

IMPACT: adds nullable invoices.supplier_registration_number and supplier_vat_id.
Existing invoices stay NULL: the vendor master cannot establish what their
original documents said. No data, tenant identifiers, or money is rewritten.
POSTCONDITION: captured seller identifiers persist independently of vendor data.
ROLLBACK: downgrade drops these two columns and their captured values; export
them before downgrading if they must be retained. Application rollback can leave
the additive columns in place.
"""

import sqlalchemy as sa
from alembic import op

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("supplier_registration_number", sa.String(80), nullable=True))
    op.add_column("invoices", sa.Column("supplier_vat_id", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "supplier_vat_id")
    op.drop_column("invoices", "supplier_registration_number")
