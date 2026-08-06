"""vat_customer_lifecycles + vat_country_activations — customer lifecycle
and per-country activation (WO-73, G2.11, R44, ADR-P3 / ADR-0023)

Two new tenant tables for the transport vertical (`BA_fleet_fuel.md`
§3.F F1/F3, §4's state tables): `vat_customer_lifecycles` — one lifecycle
row per claimant entity (`prospect → pending → active → inactive`; every
legal/claim gate keys off `active`, F1) — and `vat_country_activations` —
the per-(customer × refund country) activation ladder
(`(none) → requested → active`, each step an explicit admin click, F3).
Both key to `issuer_profiles` via the composite `(org_id, entity_id)`
RESTRICT FK (the WO-72 `vat_receipt_controls` pattern); see
`app/models/transport/customer_lifecycle.py`'s module docstring for why
these are transport-local tables, never columns on `IssuerProfile`.

New TENANT tables — RLS lands in THIS SAME migration (master-context
§4.2: no new tenant table without its RLS policy in the same migration).

Revision ID: e5b9c3d71a24
Revises: d8e4f2a61b37
Create Date: 2026-08-06 09:00:00.000000
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "e5b9c3d71a24"
down_revision: Union[str, None] = "d8e4f2a61b37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("vat_customer_lifecycles", "vat_country_activations")

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)

_CUSTOMER_CHECK = "status IN ('prospect', 'pending', 'active', 'inactive')"
_COUNTRY_CHECK = "status IN ('requested', 'active')"


def upgrade() -> None:
    op.create_table(
        "vat_customer_lifecycles",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("entity_id", app.models.base.GUID(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
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
        sa.CheckConstraint(_CUSTOMER_CHECK, name="ck_vat_customer_lifecycles_status"),
        sa.UniqueConstraint("org_id", "entity_id", name="uq_vat_customer_lifecycles_key"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_customer_lifecycles_entity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_customer_lifecycles_org_id"),
        "vat_customer_lifecycles",
        ["org_id"],
        unique=False,
    )

    op.create_table(
        "vat_country_activations",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("entity_id", app.models.base.GUID(), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
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
        sa.CheckConstraint(_COUNTRY_CHECK, name="ck_vat_country_activations_status"),
        sa.UniqueConstraint(
            "org_id", "entity_id", "country", name="uq_vat_country_activations_key"
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_country_activations_entity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_country_activations_org_id"),
        "vat_country_activations",
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

    op.drop_index(op.f("ix_vat_country_activations_org_id"), table_name="vat_country_activations")
    op.drop_table("vat_country_activations")
    op.drop_index(
        op.f("ix_vat_customer_lifecycles_org_id"), table_name="vat_customer_lifecycles"
    )
    op.drop_table("vat_customer_lifecycles")
