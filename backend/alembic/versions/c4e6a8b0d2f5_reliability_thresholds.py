"""vat_reliability_thresholds — the org's own band boundaries for the
supplier-reliability rating (WO-Q, owner decision 2026-08-08 §12)

The rating itself is DERIVED and stores nothing: every criterion is computed
on read from rows that already exist (`vat_overcharge_claims`,
`fuel_transactions.fx_source` with its stated and ECB rates,
`contract_audit`'s findings). `docs/design/supplier-reliability-rating.md`
settles that deliberately — a stored score is a score that goes stale, and
this rating must always agree with the evidence a reader can click through to.

The one fact a derivation cannot reconstruct is the org's own tolerance, so
this table holds exactly that: three boundaries, each the RECURRING edge. An
org with no row here behaves exactly as `reliability.DEFAULT_THRESHOLDS`
describes, which is why this migration seeds nothing and backfills nothing.

Four CHECKs, all defense-in-depth behind the service gate. `overcharge_cases
>= 1` (a zero would make every supplier with a clean record read `recurring`,
inverting the label); `overcharge_eur_per_1000 > 0` and `fx_markup_bps > 0`
for the same reason; and `ungoverned_share_pct` bounded to (0, 100] because a
share above 100% is not a stricter setting, it is an unreachable one that
would silently disable the criterion.

NEW TENANT table — RLS lands in THIS SAME migration (master-context §4.2: no
new tenant table without its RLS policy in the same migration).

DOWNGRADE LOSES DATA: dropping this table discards an org's typed thresholds
and returns every band to the documented defaults. No money figure anywhere is
rewritten — this table has no reader outside the reliability analysis and no
writer outside its own audited CRUD.

Revision ID: c4e6a8b0d2f5
Revises: e8f0a2b4c6d8
"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.models.base  # portable GUID type used by every table
from alembic import op

revision: str = "c4e6a8b0d2f5"
down_revision: str | None = "e8f0a2b4c6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("vat_reliability_thresholds",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "vat_reliability_thresholds",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("overcharge_cases", sa.Integer(), nullable=False),
        sa.Column("overcharge_eur_per_1000", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("fx_markup_bps", sa.Integer(), nullable=False),
        sa.Column("ungoverned_share_pct", sa.Numeric(precision=5, scale=2), nullable=False),
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
        sa.CheckConstraint(
            "overcharge_cases >= 1", name="ck_vat_reliability_thresholds_cases_positive"
        ),
        sa.CheckConstraint(
            "overcharge_eur_per_1000 > 0", name="ck_vat_reliability_thresholds_eur_positive"
        ),
        sa.CheckConstraint("fx_markup_bps > 0", name="ck_vat_reliability_thresholds_bps_positive"),
        sa.CheckConstraint(
            "ungoverned_share_pct > 0 AND ungoverned_share_pct <= 100",
            name="ck_vat_reliability_thresholds_share_range",
        ),
        sa.UniqueConstraint("org_id", name="uq_vat_reliability_thresholds_org"),
        sa.UniqueConstraint("org_id", "id", name="uq_vat_reliability_thresholds_org_id_id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_reliability_thresholds_org_id"),
        "vat_reliability_thresholds",
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
        op.f("ix_vat_reliability_thresholds_org_id"), table_name="vat_reliability_thresholds"
    )
    op.drop_table("vat_reliability_thresholds")
