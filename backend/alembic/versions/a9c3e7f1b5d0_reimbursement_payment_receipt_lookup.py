"""Index existing expense receipts for bounded payment-evidence projection.

IMPACT: one NON-unique read index on expense_commands(org_id, operation,
target_id). No table/column/constraint/data/retention or evidence change.
PREFLIGHT: use one migration writer; check this index name is absent. On
PostgreSQL the build is CONCURRENTLY outside the DDL transaction so ordinary
receipt writers are not locked out for the full build. Budget production disk
and maintenance_work_mem; wait for long transactions before deployment.
REFUSAL: an already-present same-name index is not silently accepted. A failed
concurrent build can leave an invalid index: inspect its definition/validity and
retain evidence, then deliberately drop only that failed read index concurrently
before retrying. A crash after a valid build but before revision stamping also
requires an operator to reconcile the migration state; never guess its identity.
NORMALIZATION: none. Legacy payment/receipt facts remain unchanged and unknown.
POSTCONDITION: exact three-column index; existing RLS and key uniqueness retained.
ROLLBACK/REPAIR: drop only this index (concurrently on PostgreSQL). Receipt,
audit, batch, report and job rows are never deleted or rewritten by downgrade.
"""

import sqlalchemy as sa

from alembic import op

revision = "a9c3e7f1b5d0"
down_revision = "d4e6f8a01223"
branch_labels = None
depends_on = None
INDEX = "ix_expense_commands_org_operation_target"


def upgrade():
    bind = op.get_bind()
    existing = {index["name"] for index in sa.inspect(bind).get_indexes("expense_commands")}
    if INDEX in existing:
        raise RuntimeError(
            f"{INDEX} already exists: inspect its exact definition and validity, "
            "then reconcile the interrupted migration before retrying."
        )
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.create_index(
                INDEX,
                "expense_commands",
                ["org_id", "operation", "target_id"],
                unique=False,
                postgresql_concurrently=True,
            )
    else:
        op.create_index(INDEX, "expense_commands", ["org_id", "operation", "target_id"])


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.drop_index(INDEX, table_name="expense_commands", postgresql_concurrently=True)
    else:
        op.drop_index(INDEX, table_name="expense_commands")
