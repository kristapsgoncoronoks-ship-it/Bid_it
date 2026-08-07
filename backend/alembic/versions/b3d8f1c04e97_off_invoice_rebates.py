"""vat_off_invoice_rebates — the recorded off-invoice rebate document that
merges into `net_eur_eff` (WO-84, G4.2, R50, ADR-P3 / ADR-0023)

One new tenant table for the transport vertical. `BA_fleet_fuel.md` §4.2's
two-tier discount model: an ON-invoice discount is *"already inside
`net_eur`"*, while an OFF-invoice rebate *"lands ONLY in `net_eur_eff`"* — the
canonical case being a supplier invoicing at list price while a rebate partner
issues a separate rebate invoice per country. That second document is not on
the statement any parser reads, so it has to be RECORDED; this table is the
record, and it is the ONLY thing allowed to move `net_eur_eff` away from
`net_eur`.

R50's *"guard the input source"* lands here as two database-level CHECKs:
`source_ref <> ''` and `source_party <> ''`. A rebate with no identified source
cannot exist even through a raw path — the same defense-in-depth discipline as
the `product_group` and `goods_code <> '9'` constraints. (The other half of the
guard — a rebate layer that silently DISAPPEARS — is a question about a period
with no row here, answered by `rebate.missing_source_warnings`.)

No column is added to `fuel_transactions`: `net_eur_eff` has existed and been
NOT NULL since `fc45baaf3283`, defaulting to `net_eur`. This migration
therefore needs NO backfill and changes no existing row; a stored effective net
only moves once a rebate is recorded and a close runs.

NEW TENANT table — RLS lands in THIS SAME migration (master-context §4.2: no
new tenant table without its RLS policy in the same migration).

DOWNGRADE LOSES DATA: dropping this table discards every recorded rebate
document. It deliberately does NOT revert `fuel_transactions.net_eur_eff`
values a close already merged — those are ordinary audited column data, and
silently rewriting money figures inside a schema downgrade would be the more
dangerous act (see the work order's rollback section: delete the rebate rows
and re-run the close, which recomputes from `net_eur` and audits old→new).

Revision ID: b3d8f1c04e97
Revises: a7c2e9f14b58
Create Date: 2026-08-07 12:00:00.000000
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "b3d8f1c04e97"
down_revision: Union[str, None] = "a7c2e9f14b58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("vat_off_invoice_rebates",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "vat_off_invoice_rebates",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("supplier", sa.String(length=60), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        # THE IDENTIFIED SOURCE — R50's guard, enforced at the database.
        sa.Column("source_ref", sa.String(length=120), nullable=False),
        sa.Column("source_party", sa.String(length=120), nullable=False),
        sa.Column("rebate_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount_local", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("amount_eur", sa.Numeric(precision=14, scale=2), nullable=False),
        # The platform-wide FX provenance quadruple (app/models/fx.py).
        sa.Column("fx_rate", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("fx_ecb_rate", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("fx_ecb_date", sa.Date(), nullable=True),
        sa.Column("fx_source", sa.String(length=16), nullable=True),
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
        sa.CheckConstraint("source_ref <> ''", name="ck_vat_off_invoice_rebates_source_ref"),
        sa.CheckConstraint("source_party <> ''", name="ck_vat_off_invoice_rebates_source_party"),
        sa.CheckConstraint("amount_local > 0", name="ck_vat_off_invoice_rebates_local_positive"),
        sa.CheckConstraint("amount_eur > 0", name="ck_vat_off_invoice_rebates_eur_positive"),
        sa.UniqueConstraint(
            "org_id",
            "supplier",
            "country",
            "period",
            "source_ref",
            name="uq_vat_off_invoice_rebates_key",
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_vat_off_invoice_rebates_org_id_id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_off_invoice_rebates_org_id"),
        "vat_off_invoice_rebates",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_vat_off_invoice_rebates_lookup",
        "vat_off_invoice_rebates",
        ["org_id", "supplier", "period"],
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

    op.drop_index("ix_vat_off_invoice_rebates_lookup", table_name="vat_off_invoice_rebates")
    op.drop_index(
        op.f("ix_vat_off_invoice_rebates_org_id"), table_name="vat_off_invoice_rebates"
    )
    op.drop_table("vat_off_invoice_rebates")
