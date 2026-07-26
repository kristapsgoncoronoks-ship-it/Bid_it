"""extraction fields: line-item scope (E1.2)

Adds `extraction_fields.line_index` — NULL for the five invoice header fields
(every pre-existing row, so no backfill is needed), `n` for one field of the
draft's `line_items[n]`. Extends per-field capture provenance (status,
confidence, original/normalized/reviewed values) to the money-carrying line
cells with the same honest semantics: confidence NULL = exact (deterministic
source), 0.85 PDF text-layer, 0.55 OCR.

No RLS change: `extraction_fields` is an existing tenant table whose policy
already covers every column.

Revision ID: b45eaac2f3f7
Revises: e6a8c0b2d4f6
Create Date: 2026-07-26 17:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b45eaac2f3f7"
down_revision: Union[str, None] = "e6a8c0b2d4f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("extraction_fields", schema=None) as b:
        b.add_column(sa.Column("line_index", sa.Integer(), nullable=True))


def downgrade() -> None:
    # Loses only the line scoping (rows revert to looking like header rows);
    # provenance values themselves are untouched.
    with op.batch_alter_table("extraction_fields", schema=None) as b:
        b.drop_column("line_index")
