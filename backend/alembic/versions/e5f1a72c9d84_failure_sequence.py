"""F-06 — a failure identity that cannot collide with itself

Revision ID: e5f1a72c9d84
Revises: d3b8c05f7a41
Create Date: 2026-08-14

An acknowledgement of a failed capture was pinned to a WALL-CLOCK timestamp, and
coverage was `ack.failure_seen_at >= record.failed_at`. A capture that failed
AGAIN within the same timestamp tick as its acknowledgement therefore looked
already-acknowledged and stayed hidden — a real failure the operator never saw
again, which is precisely the silence the worklist exists to break. It was
observed once as a flaky test before it was understood.

Any timestamp column collides at its own resolution, however fine. An integer
does not. `failure_seq` counts the failures of a record; an acknowledgement
stores the sequence it covers, and coverage becomes
`ack.failure_seq >= record.failure_seq`.

BACK-FILL IS DELIBERATELY ALL-ZERO. Existing failures and existing
acknowledgements both start at 0, so every acknowledgement made before this
migration keeps covering exactly the failure it covered before — no worklist
changes shape on deploy. The first NEW failure on such a record moves it to 1,
which no old acknowledgement covers, so it resurfaces correctly from then on.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e5f1a72c9d84"
down_revision: Union[str, None] = "d3b8c05f7a41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("extraction_runs", "inbound_invoices", "capture_acknowledgements")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("failure_seq", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "failure_seq")
