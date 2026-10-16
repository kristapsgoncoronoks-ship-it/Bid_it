"""N1 received-line captured and calculated amounts, additive only.

Old/approved lines remain NULL: no captured evidence or historic money is
reconstructed. New writes populate calculated snapshots. Downgrade discards
these snapshots; retain source/review evidence before removing the columns.
"""

import sqlalchemy as sa
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("line_items") as batch:
        for name in ("tax_amount", "gross_amount", "captured_tax_amount", "captured_gross_amount"):
            batch.add_column(sa.Column(name, sa.Numeric(14, 2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("line_items") as batch:
        for name in ("captured_gross_amount", "captured_tax_amount", "gross_amount", "tax_amount"):
            batch.drop_column(name)
