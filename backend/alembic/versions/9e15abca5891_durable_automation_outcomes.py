"""Add truthful pending/blocked/unknown automation outcomes.

IMPACT: extends one check constraint; no historic run is reclassified.
PREFLIGHT/REFUSAL: existing rows remain legal; no delivery or replay is attempted.
POSTCONDITION: queued intent is distinct from successful SMTP acceptance.
ROLLBACK: application rollback first; schema downgrade refuses new-state rows.
"""

import sqlalchemy as sa

from alembic import op

revision = "9e15abca5891"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("automation_runs") as batch:
        batch.drop_constraint("ck_automation_runs_status", type_="check")
        batch.create_check_constraint(
            "ck_automation_runs_status",
            "status IN ('ok', 'throttled', 'failed', 'pending', 'blocked', 'unknown')",
        )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM automation_runs WHERE status IN ('pending', 'blocked', 'unknown')"
        )
    ):
        raise RuntimeError(
            "Retain the expanded constraint while durable automation outcomes exist; do not discard or relabel evidence."
        )
    with op.batch_alter_table("automation_runs") as batch:
        batch.drop_constraint("ck_automation_runs_status", type_="check")
        batch.create_check_constraint(
            "ck_automation_runs_status", "status IN ('ok', 'throttled', 'failed')"
        )
