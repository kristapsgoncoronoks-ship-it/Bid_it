"""WO-W — webhook deliveries gain an idempotency key.

One nullable column + one PARTIAL unique index. No data change, no backfill.

WHY
-----
`webhooks.emit()` created a `WebhookDelivery` per endpoint unconditionally, so
a caller that retried delivered the same event to the customer's system twice.
`emit` is called from routes that perform real business actions — invoice
approved, expense reimbursed — and a receiver that books on those events cannot
tell a duplicate from a second occurrence. Nothing in the schema stopped it.

  webhook_deliveries.idempotency_key   (new, nullable)
  uq_webhook_deliv_idem                (new, UNIQUE, partial)

WHY THE INDEX IS PARTIAL
--------------------------
The predicate is `idempotency_key IS NOT NULL`, so unkeyed deliveries are not
covered and behave exactly as before. That is the point: the key is OPT-IN.
Postgres ignores NULLs in a unique index anyway, but SQLite's behaviour with
multiple NULLs in a composite unique index is a detail this codebase should not
be relying on implicitly — the partial predicate states the intent in the schema
instead of inheriting it from a dialect.

A caller with no natural key must not invent one. An invented key that collided
would SUPPRESS a delivery that should have happened, which is a worse failure
than the duplicate it was meant to prevent.

WHY NO BACKFILL
-----------------
Existing rows keep `idempotency_key = NULL` and stay outside the index. There is
no key to reconstruct for a delivery already made, and inventing one
retroactively could only cause a FUTURE genuine delivery to be suppressed by
colliding with history.

Revision ID: c7f9b2e4a6d1
Revises: b1d3f5a7c9e2
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c7f9b2e4a6d1"
down_revision = "b1d3f5a7c9e2"
branch_labels = None
depends_on = None

INDEX = "uq_webhook_deliv_idem"


def upgrade() -> None:
    with op.batch_alter_table("webhook_deliveries") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(length=200), nullable=True))
    op.create_index(
        INDEX,
        "webhook_deliveries",
        ["endpoint_id", "idempotency_key"],
        unique=True,
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="webhook_deliveries")
    with op.batch_alter_table("webhook_deliveries") as batch:
        batch.drop_column("idempotency_key")
