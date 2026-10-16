"""R011 — persist invoice command identities and frozen delivery intent.

IMPACT: nullable command keys/hashes on issued_invoices and email_messages;
nullable attachment references and sender on email_messages; tenant/key UNIQUE
constraints. Existing rows have NULL keys and keep their historical state.
PREFLIGHT: all newly added keys are NULL, so no historical key conflicts exist.
POSTCONDITION: a keyed create/issue/delivery can commit at most once per tenant
and command kind. No money, numbering, tenant or historical sent state is rewritten.
ROLLBACK: dropping these columns loses replay protection and delivery references;
drain/resolve queued or uncertain deliveries and retain/export command evidence
before downgrade. Application rollback may leave additive columns in place.
"""

import sqlalchemy as sa
from alembic import op

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("issued_invoices") as batch:
        for action in ("create", "issue"):
            batch.add_column(sa.Column(f"{action}_command_key", sa.String(120), nullable=True))
            batch.add_column(sa.Column(f"{action}_command_hash", sa.String(64), nullable=True))
            batch.create_unique_constraint(
                f"uq_issued_{action}_command", ["org_id", f"{action}_command_key"]
            )
    with op.batch_alter_table("email_messages") as batch:
        batch.add_column(sa.Column("is_primary", sa.Boolean(), nullable=True))
        for name, size in (
            ("command_key", 120), ("command_hash", 64), ("attachment_sha256", 64),
            ("attachment_filename", 255), ("from_email", 255),
        ):
            batch.add_column(sa.Column(name, sa.String(size), nullable=True))
        batch.create_unique_constraint("uq_email_command", ["org_id", "command_key"])
        batch.create_index("uq_email_primary_delivery", ["org_id", "invoice_id"], unique=True,
                           postgresql_where=sa.text("is_primary = true"), sqlite_where=sa.text("is_primary = 1"))


def downgrade() -> None:
    with op.batch_alter_table("email_messages") as batch:
        batch.drop_index("uq_email_primary_delivery")
        batch.drop_column("is_primary")
        batch.drop_constraint("uq_email_command", type_="unique")
        for name in ("command_key", "command_hash", "attachment_sha256", "attachment_filename", "from_email"):
            batch.drop_column(name)
    with op.batch_alter_table("issued_invoices") as batch:
        for action in ("create", "issue"):
            batch.drop_constraint(f"uq_issued_{action}_command", type_="unique")
            batch.drop_column(f"{action}_command_key")
            batch.drop_column(f"{action}_command_hash")
