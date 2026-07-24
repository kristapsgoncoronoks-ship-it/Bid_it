"""sales customer master + customer contacts, and issued_invoices.customer_id

Two new tenant-scoped tables (RLS + TENANT_TABLES) plus an optional customer link
on issued invoices.

Revision ID: a3c5e7b9d1f4
Revises: f1b3a5c7e9d0
Create Date: 2026-07-23 14:55:00.000000
"""

from typing import Sequence, Union

import app.models.base
import sqlalchemy as sa
from alembic import op

revision: str = "a3c5e7b9d1f4"
down_revision: Union[str, None] = "f1b3a5c7e9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("customers", "customer_contacts")

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)
_TS = dict(server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False)
_G = app.models.base.GUID


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("org_id", _G(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=True),
        sa.Column("vat_number", sa.String(length=32), nullable=True),
        sa.Column("registration_number", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("address_line1", sa.String(length=200), nullable=True),
        sa.Column("address_line2", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("ship_address_line1", sa.String(length=200), nullable=True),
        sa.Column("ship_address_line2", sa.String(length=200), nullable=True),
        sa.Column("ship_city", sa.String(length=120), nullable=True),
        sa.Column("ship_postal_code", sa.String(length=20), nullable=True),
        sa.Column("ship_country", sa.String(length=2), nullable=True),
        sa.Column("payment_terms_days", sa.Integer(), nullable=True),
        sa.Column("default_currency", sa.String(length=3), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", _G(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id", name="uq_customers_org_id"),
    )
    op.create_index(op.f("ix_customers_org_id"), "customers", ["org_id"], unique=False)
    op.create_index("ix_customers_org_active", "customers", ["org_id", "is_active"], unique=False)

    op.create_table(
        "customer_contacts",
        sa.Column("org_id", _G(), nullable=False),
        sa.Column("customer_id", _G(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("role", sa.String(length=80), nullable=True),
        sa.Column("id", _G(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_customer_contacts_org_id"), "customer_contacts", ["org_id"], unique=False
    )
    op.create_index(
        op.f("ix_customer_contacts_customer_id"),
        "customer_contacts",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_customer_contacts_org_customer",
        "customer_contacts",
        ["org_id", "customer_id"],
        unique=False,
    )

    with op.batch_alter_table("issued_invoices", schema=None) as b:
        b.add_column(sa.Column("customer_id", _G(), nullable=True))
        b.create_index("ix_issued_invoices_customer_id", ["customer_id"], unique=False)
        b.create_foreign_key(
            "fk_issued_invoices_customer", "customers", ["customer_id"], ["id"], ondelete="SET NULL"
        )

    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
            )


def downgrade() -> None:
    with op.batch_alter_table("issued_invoices", schema=None) as b:
        b.drop_constraint("fk_issued_invoices_customer", type_="foreignkey")
        b.drop_index("ix_issued_invoices_customer_id")
        b.drop_column("customer_id")
    op.drop_table("customer_contacts")
    op.drop_table("customers")
