"""Add durable attachment command identity, independent of content hash.

IMPACT: nullable command columns and unique tenant key; legacy rows unchanged.
PREFLIGHT/REFUSAL: no existing file can imply a historical upload command.
POSTCONDITION: retries bind one invoice, payload and metadata row.
ROLLBACK: retain columns for application rollback; downgrade discards command
lookup evidence, so refuses when keyed rows exist.
"""

import sqlalchemy as sa

from alembic import op

revision = "0d546afada3c"
down_revision = "9e15abca5891"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("issued_invoice_attachments") as batch:
        batch.add_column(sa.Column("command_key", sa.String(120), nullable=True))
        batch.add_column(sa.Column("command_hash", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_issued_attachment_command", ["org_id", "command_key"])


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM issued_invoice_attachments WHERE command_key IS NOT NULL")
    ):
        raise RuntimeError(
            "Retain attachment command evidence; do not discard committed lookup identity."
        )
    with op.batch_alter_table("issued_invoice_attachments") as batch:
        batch.drop_constraint("uq_issued_attachment_command", type_="unique")
        batch.drop_column("command_hash")
        batch.drop_column("command_key")
