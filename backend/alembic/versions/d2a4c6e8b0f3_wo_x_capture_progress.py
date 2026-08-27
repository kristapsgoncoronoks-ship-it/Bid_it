"""WO-X — extraction runs record how far along they are.

Three columns on `extraction_runs`. No constraint, no backfill, no data change.

  stage        (new, nullable)  — a code from capture_progress.STAGES
  pages_done   (new, NOT NULL, server_default '0')
  pages_total  (new, nullable)

WHY
-----
The capture poll reported four words — queued, running, parsed, failed — for
every document. A 3-page text-layer PDF and a 40-page scan look identical while
they run, so the person watching cannot tell a long job from a hung one. They
reload, re-upload, or raise a ticket about a document that was being read
correctly all along. The parser already knew which phase it was in and which
page it was on; nothing persisted it.

WHY `stage` IS NULLABLE AND NOT BACKFILLED
--------------------------------------------
An existing row keeps `stage = NULL`, which the API reports as "unknown". Every
one of those runs has already finished, so any stage written for them would be
a value nothing observed, indistinguishable in the column from one that was
measured. The absence is the honest record.

WHY `pages_done` IS NOT NULL BUT `pages_total` IS NOT
-------------------------------------------------------
"No pages have been read yet" is genuinely zero for every run, historical ones
included. "How many pages the document has" is not: it is unknown for a
historical run, and undefined for a CSV or an XML e-invoice, which have no
pages at all. A 0 there would claim a page count of zero.

Revision ID: d2a4c6e8b0f3
Revises: c7f9b2e4a6d1
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d2a4c6e8b0f3"
down_revision = "c7f9b2e4a6d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("extraction_runs") as batch:
        batch.add_column(sa.Column("stage", sa.String(length=16), nullable=True))
        batch.add_column(
            sa.Column("pages_done", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("pages_total", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("extraction_runs") as batch:
        batch.drop_column("pages_total")
        batch.drop_column("pages_done")
        batch.drop_column("stage")
