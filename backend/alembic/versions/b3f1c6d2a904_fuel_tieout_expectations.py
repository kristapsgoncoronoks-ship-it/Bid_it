"""fuel_tieout_expectations — the engine tie-out targets typed from the
invoice PDF (WO-66, G3.3, R25, ADR-P3 / ADR-0023)

One new tenant table for the transport vertical: `fuel_tieout_
expectations`. See `app/models/transport/tie_out.py`'s module docstring
for the full design rationale (why the grain carries `currency`, which
tolerances are harvested vs interpreted, why a human types the figures).

This is a new TENANT table — RLS lands in THIS SAME migration (master-
context §4.2: no new tenant table without its RLS policy in the same
migration). Composite `(org_id, entity_id)` FK into `issuer_profiles`
(§4.3), RESTRICT — deleting an entity must not silently delete its
tie-out evidence.

`app.services.transport.tie_out.set_expectation` is the only writer
(upsert by the natural key, audited old->new); `remove_expectation` the
only deleter (audited).

Revision ID: b3f1c6d2a904
Revises: 4ae197627e35
Create Date: 2026-08-02 08:40:00.000000
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "b3f1c6d2a904"
down_revision: Union[str, None] = "4ae197627e35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("fuel_tieout_expectations",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "fuel_tieout_expectations",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("entity_id", app.models.base.GUID(), nullable=False),
        sa.Column("supplier", sa.String(length=60), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        # Tolerance 0 — EXACT, always (harvested §2.1a).
        sa.Column("expected_lines", sa.Integer(), nullable=False),
        sa.Column("expected_gross_local", sa.Numeric(14, 2), nullable=True),
        sa.Column("gross_local_tolerance", sa.Numeric(4, 2), nullable=False),
        sa.Column("expected_net_eur", sa.Numeric(14, 2), nullable=True),
        sa.Column("expected_gross_eur", sa.Numeric(14, 2), nullable=True),
        sa.Column("expected_diesel_litres", sa.Numeric(14, 3), nullable=True),
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
        sa.CheckConstraint("expected_lines >= 0", name="ck_fuel_tieout_expectations_lines_nonneg"),
        # The harvested 0.02–0.05 tolerance band — defense-in-depth against
        # a raw write widening the tie-out beyond what the source allowed.
        sa.CheckConstraint(
            "gross_local_tolerance >= 0.02 AND gross_local_tolerance <= 0.05",
            name="ck_fuel_tieout_expectations_gross_tol",
        ),
        sa.UniqueConstraint(
            "org_id",
            "entity_id",
            "supplier",
            "period",
            "currency",
            name="uq_fuel_tieout_expectations_key",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_fuel_tieout_expectations_entity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_fuel_tieout_expectations_org_id"),
        "fuel_tieout_expectations",
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
        op.f("ix_fuel_tieout_expectations_org_id"), table_name="fuel_tieout_expectations"
    )
    op.drop_table("fuel_tieout_expectations")
