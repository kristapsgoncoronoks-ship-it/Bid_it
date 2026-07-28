"""fuel_transactions — the typed transaction model (WO-50, G1.2, ADR-P3 / ADR-0023)

One new tenant table for the transport vertical: `fuel_transactions`, the
typed fuel-card/toll-network line item per `BA_fleet_fuel.md` section 4.2
(the canonical schema) and section 8.1 items 4-6 ("what must not be carried
forward" — the duplicated positional schema, the overloaded `note` column,
and the missing natural key). See `app/models/transport/fuel_transaction.py`'s
module docstring for the full design rationale (why the natural key is
`(org_id, entity_id, supplier, period, line_seq)` and why `invoice_id` targets
`invoices`, not a transport-internal table).

This is a new TENANT table — RLS lands in THIS SAME migration (master-context
§4.2: no new tenant table without its RLS policy in the same migration).

No code in this order populates `invoice_id` (a future note-matching
service resolves it) or reads through to the claim-line materializer
(G2.4/G2.5) or the lock table (G2.2) — both future work orders.

Revision ID: fc45baaf3283
Revises: 02d418169f97
Create Date: 2026-07-28 20:16:07.292591
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "fc45baaf3283"
down_revision: Union[str, None] = "02d418169f97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("fuel_transactions",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "fuel_transactions",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("entity_id", app.models.base.GUID(), nullable=False),
        sa.Column("supplier", sa.String(length=60), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("line_seq", sa.Integer(), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("vehicle_ref", sa.String(length=60), nullable=False),
        sa.Column("txn_date", sa.Date(), nullable=False),
        sa.Column("txn_time", sa.String(length=20), nullable=False),
        sa.Column("station", sa.String(length=120), nullable=False),
        sa.Column("product", sa.String(length=120), nullable=False),
        sa.Column("product_group", sa.String(length=20), nullable=False),
        sa.Column("qty", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("net_local", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("vat_local", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("gross_local", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("net_eur", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("vat_eur", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("net_eur_eff", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("invoice_ref", sa.String(length=120), nullable=True),
        sa.Column("provenance_note", sa.Text(), nullable=True),
        sa.Column("invoice_id", app.models.base.GUID(), nullable=True),
        sa.Column("fx_rate", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("fx_ecb_rate", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("fx_ecb_date", sa.Date(), nullable=True),
        sa.Column("fx_source", sa.String(length=16), nullable=True),
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
        # G1.2 — the natural key. A lost race on this constraint raises
        # IntegrityError; a replay INSERT of the same source line must
        # therefore go through app.services.transport.fuel_ingest.
        # ingest_transaction, which finds the existing row rather than
        # racing a second INSERT.
        sa.UniqueConstraint(
            "org_id", "entity_id", "supplier", "period", "line_seq", name="uq_fuel_transactions_natural_key"
        ),
        # Composite-FK target for the (future) vat_claimed_invoices lock table (G2.2).
        sa.UniqueConstraint("org_id", "id", name="uq_fuel_transactions_org_id_id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_fuel_transactions_entity",
            ondelete="RESTRICT",
        ),
        # ADR-P3 rule 1 — the ONE nullable FK from transport INTO the AP
        # invoice table (never the reverse).
        sa.ForeignKeyConstraint(
            ["org_id", "invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_fuel_transactions_invoice",
            ondelete="SET NULL",
        ),
        # Defense-in-depth: derive_product_group() is the real source of
        # truth; this stops a malformed value even via a raw path.
        sa.CheckConstraint(
            "product_group IN ('Diesel', 'HVO', 'AdBlue', 'Parking', 'Toll/Fees', 'Promo adj', 'Service/Other')",
            name="ck_fuel_transactions_product_group",
        ),
        # Reuses the platform's existing fx_source convention verbatim
        # (app/models/fx.py::FX_SOURCE_CHECK) — see the model docstring for
        # why this is NOT Fleet Fuel's own narrower 3-value enum.
        sa.CheckConstraint(
            "fx_source IS NULL OR fx_source IN ('eur', 'stated', 'ecb', 'unknown')",
            name="ck_fuel_transactions_fx_source",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_fuel_transactions_org_id"), "fuel_transactions", ["org_id"], unique=False)
    op.create_index(
        "ix_fuel_transactions_org_entity", "fuel_transactions", ["org_id", "entity_id"], unique=False
    )
    op.create_index(
        "ix_fuel_transactions_org_period", "fuel_transactions", ["org_id", "period"], unique=False
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

    op.drop_index("ix_fuel_transactions_org_period", table_name="fuel_transactions")
    op.drop_index("ix_fuel_transactions_org_entity", table_name="fuel_transactions")
    op.drop_index(op.f("ix_fuel_transactions_org_id"), table_name="fuel_transactions")
    op.drop_table("fuel_transactions")
