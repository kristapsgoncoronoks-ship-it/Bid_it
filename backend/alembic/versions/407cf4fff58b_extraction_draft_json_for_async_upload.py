"""extraction_runs.draft_json — async direct-upload capture (Stage B)

Holds the serialized ParsedInvoiceDraft the worker produces, so direct-upload OCR
can run OFF the API tier and the client fetches the result afterwards. Additive,
nullable — no backfill.

Revision ID: 407cf4fff58b
Revises: efd78f4cbe2e
Create Date: 2026-07-21 18:55:34.482397
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "407cf4fff58b"
down_revision: Union[str, None] = "efd78f4cbe2e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("extraction_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("draft_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("extraction_runs", schema=None) as batch_op:
        batch_op.drop_column("draft_json")
