"""Add received-invoice supplier IBAN document evidence.

IMPACT: nullable invoices.supplier_iban; no existing payment or tenant data changes.
PREFLIGHT/REFUSAL: additive nullable column; no existing source can be inferred.
POSTCONDITION: snapshots independent of approved vendor bank/payment authority.
ROLLBACK: dropping the column loses captured evidence; prefer app rollback with
column retained. Export evidence securely before a destructive schema downgrade.
"""

import sqlalchemy as sa

from alembic import op

revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("supplier_iban", sa.String(34), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "supplier_iban")
