"""vat_fee_rates — the configured contingency-fee rate (WO-95, G2.9, R13,
ADR-P3 / ADR-0023)

One new tenant table for the transport vertical. `BA_fleet_fuel.md` §2.2 states
the arithmetic the table feeds, verbatim:

    fee = max(fee_pct% × base, fee_min)   [customer_master.compute_fee]
    rate FROZEN at submission; fee CHARGED on the PAID amount

and C11 fixes the resolution order this table's three rungs implement:
*"per-(customer, country) override → customer default → (0, 0)"*, with R40
naming the rung above it (*"Standard fee is admin-editable; a per-client fee
overrides it"*) — which is also the shape the owner's 2026-08-08 decision names
(*"the rate as an org-level setting"*).

NO SEED, NO BACKFILL, AND THAT IS THE DESIGN
----------------------------------------------
The table ships EMPTY and nothing populates it. C11's terminal `(0, 0)` rung is
deliberately NOT reproduced: `app.services.transport.fee.resolve_fee_rate`
refuses (`fee_rate_not_configured`) when no row matches, so an org with no
configured rate cannot submit a claim. The percentage and the minimum are the
two facts the owner explicitly left open on 2026-08-08, and a fee figure is a
live charge against a client — unlike the diesel-excise rate, whose default IS
a labelled placeholder on an advisory figure that asserts no eligibility.
Absence here is the correct state for every existing org, so this migration
changes no existing row and needs no reconciliation report.

UNIQUENESS IS A CONSTRAINT **PLUS** A PARTIAL INDEX
------------------------------------------------------
`entity_id` is NULL on the org-standard rung and SQL treats NULLs as distinct,
so `uq_vat_fee_rates_key` alone would permit two org standards. The partial
unique index `uq_vat_fee_rates_org_standard` (`WHERE entity_id IS NULL`) makes
"at most one standard rate per org" a database fact, and behaves identically on
SQLite and PostgreSQL. `country` is NOT NULL with `''` as the sentinel for "every
country", which keeps the two customer rungs on the ordinary UNIQUE.

Four database-level CHECKs, all defense-in-depth behind the service gate:
`fee_pct BETWEEN 0 AND 100` (a contingency fee above the whole recovery would
bill a client more than the refund), `fee_min >= 0`, `country = '' OR
length(country) = 2` (the sentinel is a sentinel, never a country), and
`entity_id IS NOT NULL OR country = ''` (no org-level per-country rung is
harvested by either C11 or R40, and a shape the specification does not describe
is not stored).

A `(0, 0)` row IS permitted — see `app/models/transport/fee_rate.py` for why
that is the opposite of, not a hole in, the fail-closed resolution: the refusal
is about ABSENCE, and an explicitly typed zero is an audited human decision.

NEW TENANT table — RLS lands in THIS SAME migration (master-context §4.2: no new
tenant table without its RLS policy in the same migration).

DOWNGRADE LOSES DATA: dropping this table discards every configured rate. No
money figure is rewritten in either direction — a claim frozen while the table
existed keeps its own `fee_pct`/`fee_min`/`fee_eur` on `vat_refund_claims`,
which this migration does not touch — but a downgraded deployment cannot submit
a new claim until rates are re-entered.

Revision ID: d4c7b1e93f27
Revises: f2a91c07d4e6
Create Date: 2026-08-10 09:00:00.000000
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "d4c7b1e93f27"
down_revision: Union[str, None] = "f2a91c07d4e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("vat_fee_rates",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "vat_fee_rates",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("entity_id", app.models.base.GUID(), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("fee_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("fee_min", sa.Numeric(precision=14, scale=2), nullable=False),
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
        sa.CheckConstraint("fee_pct >= 0 AND fee_pct <= 100", name="ck_vat_fee_rates_pct_range"),
        sa.CheckConstraint("fee_min >= 0", name="ck_vat_fee_rates_min_non_negative"),
        sa.CheckConstraint(
            "country = '' OR length(country) = 2", name="ck_vat_fee_rates_country_shape"
        ),
        sa.CheckConstraint(
            "entity_id IS NOT NULL OR country = ''",
            name="ck_vat_fee_rates_org_rung_has_no_country",
        ),
        sa.UniqueConstraint("org_id", "entity_id", "country", name="uq_vat_fee_rates_key"),
        sa.UniqueConstraint("org_id", "id", name="uq_vat_fee_rates_org_id_id"),
        sa.ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_fee_rates_entity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_vat_fee_rates_org_id"), "vat_fee_rates", ["org_id"], unique=False)
    op.create_index("ix_vat_fee_rates_org_entity", "vat_fee_rates", ["org_id", "entity_id"])
    # At most ONE org-standard rate per org — a plain UNIQUE cannot express this
    # because SQL treats NULLs as distinct. Partial indexes behave identically on
    # SQLite and PostgreSQL.
    op.create_index(
        "uq_vat_fee_rates_org_standard",
        "vat_fee_rates",
        ["org_id"],
        unique=True,
        sqlite_where=sa.text("entity_id IS NULL"),
        postgresql_where=sa.text("entity_id IS NULL"),
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

    op.drop_index("uq_vat_fee_rates_org_standard", table_name="vat_fee_rates")
    op.drop_index("ix_vat_fee_rates_org_entity", table_name="vat_fee_rates")
    op.drop_index(op.f("ix_vat_fee_rates_org_id"), table_name="vat_fee_rates")
    op.drop_table("vat_fee_rates")
