"""R012 durable cash commands and tenant-safe explained reversal links.

Historical entries retain NULL command/reversal fields; no balances are rewritten.
Downgrade loses replay and reversal identity: export command evidence and resolve
uncertain responses first. An application rollback can retain additive columns.
"""

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("payments") as batch:
        batch.add_column(sa.Column("command_key", sa.String(120), nullable=True))
        batch.add_column(sa.Column("command_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("reversal_of_id", GUID(), nullable=True))
        batch.create_unique_constraint("uq_payments_org_id", ["org_id", "id"])
        batch.create_unique_constraint("uq_payments_command", ["org_id", "command_key"])
        batch.create_unique_constraint("uq_payments_reversal", ["org_id", "reversal_of_id"])
        batch.create_foreign_key(
            "fk_payments_reversal",
            "payments",
            ["org_id", "reversal_of_id"],
            ["org_id", "id"],
            deferrable=True,
            initially="DEFERRED",
        )


def downgrade() -> None:
    with op.batch_alter_table("payments") as batch:
        batch.drop_constraint("fk_payments_reversal", type_="foreignkey")
        for name in ("uq_payments_reversal", "uq_payments_command", "uq_payments_org_id"):
            batch.drop_constraint(name, type_="unique")
        for name in ("reversal_of_id", "command_hash", "command_key"):
            batch.drop_column(name)
