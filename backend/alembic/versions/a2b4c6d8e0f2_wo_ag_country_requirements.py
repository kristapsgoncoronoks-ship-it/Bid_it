"""vat_country_requirements — F3's per-country required-document set (WO-AG)

`BA_fleet_fuel.md` §3.F F3: country activation is per (customer, refund
country) "with its own required-document set (`country_requirements`, default
`["power_of_attorney"]`)". The harvested helper `country_ready_to_activate` was
deferred until the customer-document store existed (WO-AB shipped it); this
table is the one fact that store cannot derive — which kinds THIS org requires
for THAT country. No rows for a country means the default, so this migration
seeds nothing.

INFORMATIONAL ONLY (F3, verbatim): the readiness helper reads it; the
activation gate never does. Activation stays an explicit admin click.

NEW TENANT table — RLS lands in THIS SAME migration (master-context §4.2).

DOWNGRADE LOSES DATA: dropping the table returns every country to the default
set. No claim, gate or money figure reads it.

Revision ID: a2b4c6d8e0f2
Revises: f1a2b3c4d5e6
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

import app.models.base  # portable GUID type used by every table
from alembic import op

revision: str = "a2b4c6d8e0f2"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("vat_country_requirements",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "vat_country_requirements",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
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
        sa.CheckConstraint("kind IN ('power_of_attorney', 'vat_certificate', 'tax_mandate', 'fleet_list', 'company_extract', 'signatory_id', 'signed_contract', 'trade_registry')", name="ck_vat_country_requirements_kind"),
        sa.UniqueConstraint("org_id", "country", "kind", name="uq_vat_country_requirements_key"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_country_requirements_org_id"),
        "vat_country_requirements",
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
        op.f("ix_vat_country_requirements_org_id"), table_name="vat_country_requirements"
    )
    op.drop_table("vat_country_requirements")
