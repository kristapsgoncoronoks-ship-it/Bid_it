"""audit events record the request's IP and session

Revision ID: c9e4f1a7b2d8
Revises: a4d7e0c16b93
Create Date: 2026-08-16

P2-2 of docs/audit/2026-08-16-bug-scan.md. The trail recorded who, what and
when but never FROM WHERE — the owner's deletion-trail requirement names the
location explicitly, and the IP already captured on `sessions` could not be
tied to an event (one actor with two live sessions is unresolvable).

COLUMNS, not `meta`: these fields sit inside the hash chain. An IP recorded in
optional caller payload could be edited without breaking the chain, which
defeats the reason for recording it.

Additive and inert: both columns are nullable, and the hash covers them only
when present (append-if-present), so every chain written before this revision
still verifies byte-for-byte. No backfill — a location that was never captured
cannot honestly be invented after the fact.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c9e4f1a7b2d8"
down_revision: Union[str, None] = "a4d7e0c16b93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("ip", sa.String(64), nullable=True))
    op.add_column("audit_events", sa.Column("session_id", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_events", "session_id")
    op.drop_column("audit_events", "ip")
