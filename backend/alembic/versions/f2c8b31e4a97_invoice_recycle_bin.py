"""the recycle bin — invoices.deleted_at / deleted_by

Revision ID: f2c8b31e4a97
Revises: e5f1a72c9d84
Create Date: 2026-08-14

Step 1 of docs/design/deletion-and-archive.md: the columns and the central rule
that hides a binned record everywhere. NOTHING sets these columns yet — no route
soft-deletes, so on deploy this is inert and every invoice stays live. The
delete paths move onto it in a later step, once the Trash screen exists to get a
record back out; shipping the hiding before the recovering would mean a record
could vanish with nowhere to retrieve it from.

`deleted_at` is indexed because it appears in the predicate the guard adds to
EVERY select touching invoices — an unindexed column in a universal filter is a
sequential scan on the busiest table in the product.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f2c8b31e4a97"
down_revision: Union[str, None] = "e5f1a72c9d84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invoices", sa.Column("deleted_by", sa.String(length=320), nullable=True))
    op.create_index("ix_invoices_deleted_at", "invoices", ["deleted_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_invoices_deleted_at", table_name="invoices")
    op.drop_column("invoices", "deleted_by")
    op.drop_column("invoices", "deleted_at")
