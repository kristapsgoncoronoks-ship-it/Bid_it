"""vat_supplier_contract_terms + vat_overcharge_claims — supplier overcharge
detection and claim-back (WO-82, G4.5, R41, ADR-P3 / ADR-0023)

Two new tenant tables for the transport vertical:
`vat_supplier_contract_terms` — the agreed commercial term a supplier can
breach (`BA_fleet_fuel.md` §4.4's `supplier_discounts` grain; §2.5's "two term
types only": `expected_discount_eur_l` and `max_net_eur_l`, both €/L) — and
`vat_overcharge_claims` — the per-(supplier × period) claim-back lifecycle
`detected → packaged → claimed → recovered | rejected | written_off` (§4.5,
R41). See the two model docstrings for the grain, the precision choice and the
frozen-figure rationale.

Neither table references `issuer_profiles`: both are keyed on the free-text
fuel-card supplier code (the WO-61/WO-72 registry precedent), and R41's grain
names only the supplier and the period.

New TENANT tables — RLS lands in THIS SAME migration (master-context §4.2: no
new tenant table without its RLS policy in the same migration).

DOWNGRADE LOSES DATA: dropping these tables discards every configured contract
term and the whole claim-back history (including booked `recovered_eur`).
Nothing outside these two tables is touched, so no VAT claim, claim line,
invoice lock or audit chain can be affected by either direction.

Revision ID: a7c2e9f14b58
Revises: e5b9c3d71a24
Create Date: 2026-08-07 09:00:00.000000
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "a7c2e9f14b58"
down_revision: Union[str, None] = "e5b9c3d71a24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("vat_supplier_contract_terms", "vat_overcharge_claims")

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)

# `app.models.transport.fuel_transaction.PRODUCT_GROUPS`, verbatim
# (`BA_fleet_fuel.md` §4.2 row 8) — spelled out here rather than imported so a
# later change to the constant cannot silently rewrite a shipped migration.
_PRODUCT_GROUP_CHECK = (
    "product_group IN ('Diesel', 'HVO', 'AdBlue', 'Parking', 'Toll/Fees', "
    "'Promo adj', 'Service/Other')"
)
_TERM_PRESENT_CHECK = "expected_discount_eur_l IS NOT NULL OR max_net_eur_l IS NOT NULL"

# `BA_fleet_fuel.md` §4.5 / R41's six harvested claim-back states.
_STATUS_CHECK = (
    "status IN ('detected', 'packaged', 'claimed', 'recovered', 'rejected', 'written_off')"
)


def upgrade() -> None:
    op.create_table(
        "vat_supplier_contract_terms",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("supplier", sa.String(length=60), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("station_like", sa.String(length=120), nullable=False),
        sa.Column("product_group", sa.String(length=20), nullable=False),
        # €/L RATES, not amounts — 4 dp (the harvested TOLERANCE is 0.005 €/L).
        sa.Column("expected_discount_eur_l", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("max_net_eur_l", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint(_PRODUCT_GROUP_CHECK, name="ck_vat_supplier_contract_terms_product_group"),
        sa.CheckConstraint(_TERM_PRESENT_CHECK, name="ck_vat_supplier_contract_terms_has_a_term"),
        sa.UniqueConstraint(
            "org_id",
            "supplier",
            "country",
            "station_like",
            "product_group",
            name="uq_vat_supplier_contract_terms_key",
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_vat_supplier_contract_terms_org_id_id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_supplier_contract_terms_org_id"),
        "vat_supplier_contract_terms",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_vat_supplier_contract_terms_lookup",
        "vat_supplier_contract_terms",
        ["org_id", "supplier", "country"],
        unique=False,
    )

    op.create_table(
        "vat_overcharge_claims",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("supplier", sa.String(length=60), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("detected_eur", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("lines_count", sa.Integer(), nullable=False),
        sa.Column("recovered_eur", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.CheckConstraint(_STATUS_CHECK, name="ck_vat_overcharge_claims_status"),
        sa.CheckConstraint("detected_eur > 0", name="ck_vat_overcharge_claims_detected_positive"),
        sa.CheckConstraint(
            "recovered_eur IS NULL OR recovered_eur > 0",
            name="ck_vat_overcharge_claims_recovered_positive",
        ),
        sa.CheckConstraint("lines_count >= 0", name="ck_vat_overcharge_claims_lines_nonneg"),
        sa.UniqueConstraint("org_id", "supplier", "period", name="uq_vat_overcharge_claims_key"),
        sa.UniqueConstraint("org_id", "id", name="uq_vat_overcharge_claims_org_id_id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_overcharge_claims_org_id"),
        "vat_overcharge_claims",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_vat_overcharge_claims_org_period",
        "vat_overcharge_claims",
        ["org_id", "period"],
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

    op.drop_index("ix_vat_overcharge_claims_org_period", table_name="vat_overcharge_claims")
    op.drop_index(op.f("ix_vat_overcharge_claims_org_id"), table_name="vat_overcharge_claims")
    op.drop_table("vat_overcharge_claims")
    op.drop_index(
        "ix_vat_supplier_contract_terms_lookup", table_name="vat_supplier_contract_terms"
    )
    op.drop_index(
        op.f("ix_vat_supplier_contract_terms_org_id"), table_name="vat_supplier_contract_terms"
    )
    op.drop_table("vat_supplier_contract_terms")
