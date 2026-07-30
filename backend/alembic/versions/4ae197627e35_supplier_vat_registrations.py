"""supplier_vat_registrations — per-country supplier legal-entity
registration (WO-61, G3.1 slice 1, R21/R22, ADR-P3 / ADR-0023)

One new tenant table for the transport vertical: `supplier_vat_
registrations`. See `app/models/transport/supplier_registration.py`'s
module docstring for the full design rationale (why `entity_name` is a
plain string, why `source="admin"` always wins over `source="capture"`).

This is a new TENANT table — RLS lands in THIS SAME migration (master-
context §4.2: no new tenant table without its RLS policy in the same
migration).

`app.services.transport.supplier_entity.set_registration` is the only
admin-curated writer (always wins); `learn_registration` is the only
automatic writer, and only ever INSERTS — it never updates an existing
row of either source.

Revision ID: 4ae197627e35
Revises: 920cbde1e481
Create Date: 2026-07-30 04:14:04.212028
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "4ae197627e35"
down_revision: Union[str, None] = "920cbde1e481"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("supplier_vat_registrations",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "supplier_vat_registrations",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("supplier", sa.String(length=60), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("vat_number", sa.String(length=32), nullable=False),
        sa.Column("entity_name", sa.String(length=200), nullable=False),
        # "admin" (curated, always wins) | "capture" (learned, only ever
        # seeds a NEW row — never overwrites an existing one of either kind).
        sa.Column("source", sa.String(length=12), nullable=False),
        sa.Column("id", app.models.base.GUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        # R21 — one registration per (org, supplier, country); marker-only,
        # never a fuzzy match.
        sa.UniqueConstraint(
            "org_id", "supplier", "country", name="uq_supplier_vat_registrations_key"
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_supplier_vat_registrations_org_id"),
        "supplier_vat_registrations",
        ["org_id"],
        unique=False,
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
    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")

    op.drop_index(
        op.f("ix_supplier_vat_registrations_org_id"), table_name="supplier_vat_registrations"
    )
    op.drop_table("supplier_vat_registrations")
