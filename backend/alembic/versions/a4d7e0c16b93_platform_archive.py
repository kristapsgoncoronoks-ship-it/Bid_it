"""the platform archive — archived_invoices

Revision ID: a4d7e0c16b93
Revises: f2c8b31e4a97
Create Date: 2026-08-15

Step 6 of docs/design/deletion-and-archive.md. When an invoice's 30-day recycle
bin expires, the row is destroyed from `invoices` and a copy lands here instead,
where the client's own company owner can still view and download it.

A SEPARATE TABLE rather than an `is_archived` flag on `invoices`: a flag means
every query, export and support tool in the product can reach archived data, and
one forgotten filter surfaces a record the client believes they deleted.

`expires_at` is stamped at write time rather than derived from the retention
setting on read, so lowering that setting can never reach backwards and destroy
records already kept under a longer promise — which matters because retention is
a paid dimension.

Additive and inert on deploy: nothing reads or writes the table until the purge
runs, and an empty archive behaves exactly as no archive did.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

from app.models.base import GUID

revision: str = "a4d7e0c16b93"
down_revision: Union[str, None] = "f2c8b31e4a97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables this migration puts under Postgres row-level security. Declared here and
# aggregated by `tests/test_rls.py::test_rls_migration_covers_every_tenant_table`,
# which asserts the union across all migrations equals `TENANT_MODELS` exactly —
# so a new tenant table cannot ship with only the app-layer guard.
#
# That guard caught this table: the first version of this migration shipped
# layers 1 (per-query filters) and 2 (the ORM hook) and forgot layer 3. On the
# archive of all tables, "the database itself will not hand another tenant's rows
# to a raw query" is the layer that matters most — it holds the records clients
# believe they deleted.
TENANT_TABLES = ("archived_invoices",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "archived_invoices",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("org_id", GUID(), nullable=False),
        sa.Column("original_invoice_id", GUID(), nullable=False),
        sa.Column("invoice_number", sa.String(length=120), nullable=True),
        sa.Column("vendor_id", GUID(), nullable=True),
        sa.Column("vendor_name", sa.String(length=255), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=True),
        sa.Column("tax_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("total", sa.Numeric(14, 2), nullable=True),
        sa.Column("line_items_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("original_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("original_deleted_by", sa.String(length=320), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_archived_invoices_org", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_archived_invoices_org_id", "archived_invoices", ["org_id"])
    op.create_index(
        "ix_archived_invoices_original_invoice_id", "archived_invoices", ["original_invoice_id"]
    )
    op.create_index("ix_archived_invoices_invoice_number", "archived_invoices", ["invoice_number"])
    # The client-owner archive screen: one org, newest first.
    op.create_index(
        "ix_archived_invoices_org_archived", "archived_invoices", ["org_id", "archived_at"]
    )
    # The expiry sweep, and the notice that must run BEFORE it.
    op.create_index("ix_archived_invoices_expires", "archived_invoices", ["expires_at"])

    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            # FORCE so the policy applies to the table OWNER too — without it a
            # superuser-ish connection silently bypasses the whole layer.
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_index("ix_archived_invoices_expires", table_name="archived_invoices")
    op.drop_index("ix_archived_invoices_org_archived", table_name="archived_invoices")
    op.drop_index("ix_archived_invoices_invoice_number", table_name="archived_invoices")
    op.drop_index("ix_archived_invoices_original_invoice_id", table_name="archived_invoices")
    op.drop_index("ix_archived_invoices_org_id", table_name="archived_invoices")
    op.drop_table("archived_invoices")
