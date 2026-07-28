"""transport vat claim grain — M3 opener (WO-49, ADR-P3 / ADR-0023)

Two new tenant tables for the transport vertical's foundational slice:

- `vat_refund_claims` — the claim aggregate root, keyed
  `(org_id, entity_id, refund_country, ref_period)` (R1). `entity_id` is a
  composite FK into the EXISTING `issuer_profiles` registry (RESTRICT — never
  drop a legal entity a claim still references) rather than a new legal-entity
  table; see `app/models/transport/vat_claim.py` module docstring for why.
- `vat_claim_lines` — the (future-materialized) claim lines (R2), with a
  nullable composite FK to the AP `invoices` table it was resolved from
  (ADR-P3 rule 1: transport never adds a column to `invoices`; the FK runs
  the other way).

Both are new TENANT tables — RLS lands in THIS SAME migration (master-context
§4.2: no new tenant table without its RLS policy in the same migration).

Neither table is populated by any code in this order: the freeze-at-
submission service, the lock table (`vat_claimed_invoices`), and every gate
that reads these rows are future work orders (ARCH_plan.md G2.2 onward).

Revision ID: 02d418169f97
Revises: 5b295223466f
Create Date: 2026-07-28 18:51:49.308442
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "02d418169f97"
down_revision: Union[str, None] = "5b295223466f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("vat_refund_claims", "vat_claim_lines")

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "vat_refund_claims",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("entity_id", app.models.base.GUID(), nullable=False),
        sa.Column("refund_country", sa.String(length=2), nullable=False),
        sa.Column("ref_period", sa.String(length=9), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("status_code", sa.String(length=4), nullable=True),
        sa.Column("status_note", sa.Text(), nullable=True),
        sa.Column("decision_date", sa.Date(), nullable=True),
        sa.Column("action_deadline", sa.Date(), nullable=True),
        sa.Column("submitted_date", sa.Date(), nullable=True),
        sa.Column("approved_date", sa.Date(), nullable=True),
        sa.Column("paid_date", sa.Date(), nullable=True),
        sa.Column("paid_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("vat_eur", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("vat_local", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("fee_pct", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("fee_min", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("fee_eur", sa.Numeric(precision=14, scale=2), nullable=True),
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
        # R1 — the claim grain. A lost race on this constraint raises
        # IntegrityError; creating "two" claims with the same key must
        # therefore go through app.services.transport.claim.get_or_create_claim,
        # which finds the existing row rather than racing a second INSERT.
        sa.UniqueConstraint(
            "org_id", "entity_id", "refund_country", "ref_period", name="uq_vat_refund_claims_grain"
        ),
        # Composite-FK target for the (future) vat_claim_lines / vat_claimed_invoices tables.
        sa.UniqueConstraint("org_id", "id", name="uq_vat_refund_claims_org_id_id"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_refund_claims_entity",
            ondelete="RESTRICT",
        ),
        # Defense-in-depth on the period SHAPE — the service layer
        # (validate_ref_period) is the real validator; this stops a malformed
        # value even via a raw path. LIKE, not regex, for SQLite/Postgres portability.
        sa.CheckConstraint(
            "ref_period LIKE '____-Q_' OR ref_period LIKE '____-YEAR'",
            name="ck_vat_refund_claims_ref_period_shape",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_vat_refund_claims_org_id"), "vat_refund_claims", ["org_id"], unique=False)
    op.create_index(
        "ix_vat_refund_claims_org_entity", "vat_refund_claims", ["org_id", "entity_id"], unique=False
    )

    op.create_table(
        "vat_claim_lines",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("claim_id", app.models.base.GUID(), nullable=False),
        sa.Column("invoice_ref", sa.String(length=120), nullable=False),
        sa.Column("vat_id", sa.String(length=32), nullable=True),
        sa.Column("invoice_id", app.models.base.GUID(), nullable=True),
        sa.Column("goods_code", sa.String(length=2), nullable=True),
        sa.Column("product_group", sa.String(length=60), nullable=True),
        sa.Column("net_eur", sa.Numeric(precision=14, scale=2), nullable=False, server_default="0"),
        sa.Column("vat_eur", sa.Numeric(precision=14, scale=2), nullable=False, server_default="0"),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "claim_id"],
            ["vat_refund_claims.org_id", "vat_refund_claims.id"],
            name="fk_vat_claim_lines_claim",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_vat_claim_lines_invoice",
            ondelete="SET NULL",
        ),
        # R11, belt-and-braces: code "9" (luxuries/entertainment) must never
        # reach storage even via a raw path — the historical Fleet Fuel bug
        # this rule exists to prevent. NULL = not yet classified.
        sa.CheckConstraint(
            "goods_code IS NULL OR goods_code <> '9'", name="ck_vat_claim_lines_goods_code_never_9"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_vat_claim_lines_org_id"), "vat_claim_lines", ["org_id"], unique=False)
    op.create_index("ix_vat_claim_lines_org_claim", "vat_claim_lines", ["org_id", "claim_id"], unique=False)

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

    op.drop_index("ix_vat_claim_lines_org_claim", table_name="vat_claim_lines")
    op.drop_index(op.f("ix_vat_claim_lines_org_id"), table_name="vat_claim_lines")
    op.drop_table("vat_claim_lines")

    op.drop_index("ix_vat_refund_claims_org_entity", table_name="vat_refund_claims")
    op.drop_index(op.f("ix_vat_refund_claims_org_id"), table_name="vat_refund_claims")
    op.drop_table("vat_refund_claims")
