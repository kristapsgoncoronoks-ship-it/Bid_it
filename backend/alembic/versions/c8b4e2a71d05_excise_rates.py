"""vat_excise_rates — the admin override of a state's diesel-excise refund rate
(WO-91, G4.6, R42, ADR-P3 / ADR-0023)

One new tenant table for the transport vertical. `BA_fleet_fuel.md` §2.4's
`/excise` row: *"7 countries (BE·FR·IT·SI·HU·ES·HR); `litres × rate/1,000L`;
rate default **EUR 30/1,000 L is an explicit PLACEHOLDER** in the reported
EUR 25-33 band, admin-overridable."* R42 makes the override a requirement:
*"Rates are admin-overridable and the figure asserts NO eligibility."*

The DEFAULT is a code constant, not a seeded row: an org with no row here
behaves exactly as the harvested placeholder describes, so this migration needs
NO backfill and changes no existing row. A row appears only when an operator
types the rate their customs authority actually applies — which §9.2 item 13
records as an open owner question (*"Who owns the real per-country statutory
rates, and how often do they change (quarterly, per the research)?"*).

Two database-level CHECKs, both defense-in-depth behind the service gate:
`rate_eur_per_1000l > 0` (a nil rate is the ABSENCE of the regime, which the
seven-country membership already expresses — storing it as a zero would make
the analysis publish a EUR 0.00 finding, a materially different and false
claim from "we hold no rate for this state") and `country <> ''`.

The table stores a RATE, not an amount, so the scale is `Numeric(12, 4)` — the
`vat_supplier_contract_terms` precedent applied to a per-1,000-litre
denominator. Nothing here is a money amount; the euro the analysis reports is
quantized once, in the service, from the exact litre total.

This table asserts NOTHING about eligibility. `BA_fleet_fuel.md` §3.L:
*"`excise.py` | **Asserts NO eligibility** (vehicle >=7.5t / carrier
registration not modelled); rates are indicative defaults."*

NEW TENANT table — RLS lands in THIS SAME migration (master-context §4.2: no
new tenant table without its RLS policy in the same migration).

DOWNGRADE LOSES DATA: dropping this table discards every typed rate override.
It is otherwise safe — the code defaults are unaffected, the analysis keeps
producing exactly the harvested placeholder figure, and no money figure
anywhere is rewritten (this table has no writer other than the excise rate CRUD
and no reader other than the read-only excise analysis).

Revision ID: c8b4e2a71d05
Revises: a7f2e9c41b83
Create Date: 2026-08-08 09:00:00.000000
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "c8b4e2a71d05"
down_revision: Union[str, None] = "a7f2e9c41b83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("vat_excise_rates",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "vat_excise_rates",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("rate_eur_per_1000l", sa.Numeric(precision=12, scale=4), nullable=False),
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
        sa.CheckConstraint("rate_eur_per_1000l > 0", name="ck_vat_excise_rates_rate_positive"),
        sa.CheckConstraint("country <> ''", name="ck_vat_excise_rates_country_present"),
        sa.UniqueConstraint("org_id", "country", name="uq_vat_excise_rates_key"),
        sa.UniqueConstraint("org_id", "id", name="uq_vat_excise_rates_org_id_id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_excise_rates_org_id"), "vat_excise_rates", ["org_id"], unique=False
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

    op.drop_index(op.f("ix_vat_excise_rates_org_id"), table_name="vat_excise_rates")
    op.drop_table("vat_excise_rates")
