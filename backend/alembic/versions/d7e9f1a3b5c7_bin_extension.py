"""WO-M: the recycle bin extends to all entities + nothing else.

Revision ID: d7e9f1a3b5c7
Revises: c6d8e0f2a4b6
Create Date: 2026-08-26

Owner decision 2026-08-15 ("bin extends to all entities"): expense reports,
expense inbox transactions, recurring schedules and issued-invoice
attachments gain the same `deleted_at`/`deleted_by` stamp invoices carry —
stamped instead of destroyed, hidden by the ORM guard's soft-delete
criteria, restorable for the bin window, purged by the same daily job.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7e9f1a3b5c7"
down_revision: Union[str, None] = "c6d8e0f2a4b6"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

_TABLES = (
    "expense_reports",
    "expense_transactions",
    "recurring_invoices",
    "issued_invoice_attachments",
)


def upgrade() -> None:
    for t in _TABLES:
        op.add_column(
            t, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
        )
        op.add_column(t, sa.Column("deleted_by", sa.String(320), nullable=True))


def downgrade() -> None:
    for t in reversed(_TABLES):
        op.drop_column(t, "deleted_by")
        op.drop_column(t, "deleted_at")
