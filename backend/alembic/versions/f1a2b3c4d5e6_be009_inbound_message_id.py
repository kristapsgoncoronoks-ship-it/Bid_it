"""BE-009 (audit 2026-09-05) — `inbound_invoices.message_id`.

The inbound-email webhook had no message-level idempotency: a provider retry
(a timeout on our side, a 5xx, a queue replay) re-delivered the same message
and every attachment was stored again as a fresh inbox row. The provider's
Message-ID is now stored and a redelivery is acknowledged without a write.

Additive: one nullable column and a (org_id, message_id) index. RLS on the
table is unchanged (row policy, not column). Downgrade drops both.

Revision ID: f1a2b3c4d5e6
Revises: e6a8c0d2f4b6
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e6a8c0d2f4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("inbound_invoices", sa.Column("message_id", sa.String(length=255), nullable=True))
    op.create_index(
        "ix_inbound_invoices_org_message", "inbound_invoices", ["org_id", "message_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_inbound_invoices_org_message", table_name="inbound_invoices")
    op.drop_column("inbound_invoices", "message_id")
