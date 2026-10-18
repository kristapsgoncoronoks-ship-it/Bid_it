"""Fence direct-upload capture retries by durable generation.

IMPACT: additive non-null integer, existing captures remain generation zero.
PREFLIGHT: quiesce API/extract workers; old workers cannot enforce this fence.
POSTCONDITION: each new retry and its keyed job commit together.
ROLLBACK: retain column for application rollback; do not revive old workers on
live queues. Downgrade refuses once later generations exist to preserve identity.
"""

import sqlalchemy as sa

from alembic import op

revision = "127bc9ad604e"
down_revision = "0d546afada3c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "extraction_runs",
        sa.Column("attempt_seq", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM extraction_runs WHERE attempt_seq > 0")):
        raise RuntimeError("Retain capture attempt identity; quiesce and reconcile jobs first.")
    op.drop_column("extraction_runs", "attempt_seq")
