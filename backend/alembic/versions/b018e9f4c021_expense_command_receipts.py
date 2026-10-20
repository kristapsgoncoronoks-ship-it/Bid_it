"""R010 additive command receipts and exact import provenance.

IMPACT: two tenant tables, nullable source fields and candidate version1.
PREFLIGHT: all legacy source fields start NULL; no historical imports are inferred.
REFUSAL: downgrade with receipts or batches refuses to discard replay evidence.
NORMALIZATION: none; no money, currency, account or source identity is rewritten.
POSTCONDITION: scoped command and exact-source uniqueness, tenant-safe batch FK.
ROLLBACK/REPAIR: retain additive schema for application rollback; disable candidate
writers that do not obey the locking/epoch contract. Resolve/export receipts
before a separately reviewed destructive downgrade. New indexes can lock tables.
"""

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision = "b018e9f4c021"
down_revision = "0d546afada3c"
branch_labels = None
depends_on = None
TENANT_TABLES = ("expense_commands", "expense_import_batches")


def _common():
    return [
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "org_id", GUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "employee_id", GUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "expense_import_batches",
        *_common(),
        sa.Column("account_reference", sa.String(120), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("statement_currency", sa.String(3), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("credit_count", sa.Integer(), nullable=False),
        sa.Column("warnings_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("org_id", "id", name="uq_expense_import_batch_org_id"),
        sa.UniqueConstraint(
            "org_id", "employee_id", "account_reference", "source_sha256",
            name="uq_expense_import_source",
        ),
    )
    op.create_table(
        "expense_commands",
        *_common(),
        sa.Column("command_key", sa.String(120), nullable=False),
        sa.Column("operation", sa.String(24), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("target_id", GUID(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "org_id", "employee_id", "command_key", name="uq_expense_command_identity"
        ),
    )
    for table in TENANT_TABLES:
        op.create_index(f"ix_{table}_org_id", table, ["org_id"])
    with op.batch_alter_table("expense_transactions") as batch:
        batch.add_column(
            sa.Column("version", sa.Integer(), server_default="1", nullable=False)
        )
        batch.add_column(sa.Column("import_batch_id", GUID(), nullable=True))
        batch.add_column(sa.Column("source_row", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("currency_source", sa.String(16), nullable=True))
        batch.create_foreign_key(
            "fk_expense_transaction_batch", "expense_import_batches",
            ["org_id", "import_batch_id"], ["org_id", "id"], ondelete="RESTRICT",
        )
        batch.create_unique_constraint(
            "uq_expense_transaction_source_row", ["org_id", "import_batch_id", "source_row"]
        )
    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            predicate = (
                "NULLIF(current_setting('app.current_org', true), '') IS NULL "
                "OR org_id::text = NULLIF(current_setting('app.current_org', true), '')"
            )
            op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
            op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
            op.execute(sa.text(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                f"USING ({predicate}) WITH CHECK ({predicate})"
            ))


def downgrade() -> None:
    for table in TENANT_TABLES:
        count = op.get_bind().scalar(sa.text(f'SELECT count(*) FROM "{table}"'))
        if count:
            raise RuntimeError(f"R010 receipt retention refusal: {table} contains {count} rows")
    with op.batch_alter_table("expense_transactions") as batch:
        batch.drop_constraint("uq_expense_transaction_source_row", type_="unique")
        batch.drop_constraint("fk_expense_transaction_batch", type_="foreignkey")
        for column in ("currency_source", "source_row", "import_batch_id", "version"):
            batch.drop_column(column)
    for table in TENANT_TABLES:
        op.drop_table(table)
