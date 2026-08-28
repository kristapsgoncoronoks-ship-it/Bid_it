"""WO-AB — the claimant checklist's four missing rules.

One NEW TENANT table, with FORCE ROW LEVEL SECURITY in this same migration,
and one nullable column on an existing table.

  vat_claimant_documents          (new)
  issuer_profiles.nace_code       (new column)

WHY
-----
`checklist.DEFAULT_RULES` has shipped two of the six rules `BA_fleet_fuel.md`
§3.E names, with a documented reason: the other four are `check_type=
"document"` (needing a document-requirements-with-EXPIRY concept) or need a
`nace_code` column. This migration is that concept and that column, so the
harvest table can be seeded whole instead of staying a four-line deferral.

WHY `country` IS NOT NULLABLE
-------------------------------
A power of attorney is held per refund country; a contract is held once. Both
live in this table, so the unique key has to cover both. In Postgres NULL never
equals NULL, so a nullable `country` would let the same contract be inserted
any number of times while LOOKING constrained — the unique index would be
decorative. `''` is a real value that compares, and
`ck_vat_claimant_documents_country` keeps it from ever holding a one-letter
fragment.

WHY THE UNIQUE KEY CARRIES `sha256`
-------------------------------------
Re-uploading the same bytes is idempotent (the row is touched). A RENEWAL is
different bytes and takes its own row, so the lapsed document stays visible
beside the one that replaced it — which is what an operator needs to see that
a gap was CLOSED, not merely that it is closed now.

NO BACKFILL, DELIBERATELY
---------------------------
Every existing claimant gets zero documents and a NULL `nace_code`, and the
four new checklist rules therefore read as failing until each is supplied.
That is the correct answer, not a migration gap: the system does not know
whether a workspace holds a power of attorney, and writing a value that
nothing observed is the defect `d2a4c6e8b0f3` refused for `extraction_runs.
stage`. A workspace that does not want a rule deactivates it (R45).

Revision ID: a9c1e3f5b7d2
Revises: a3c5e7f9b1d4
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

import app.models.base
from alembic import op

revision: str = "a9c1e3f5b7d2"
down_revision: str | None = "a3c5e7f9b1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("vat_claimant_documents",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)

_KIND_CHECK = (
    "kind IN ('power_of_attorney', 'vat_certificate', 'tax_mandate', 'fleet_list', "
    "'company_extract', 'signatory_id', 'signed_contract', 'trade_registry')"
)
_COUNTRY_CHECK = "country = '' OR length(country) = 2"


def upgrade() -> None:
    op.add_column("issuer_profiles", sa.Column("nace_code", sa.String(length=16), nullable=True))

    op.create_table(
        "vat_claimant_documents",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("entity_id", app.models.base.GUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("country", sa.String(length=2), server_default="", nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("mime", sa.String(length=80), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("uploaded_by", sa.String(length=320), nullable=True),
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
        sa.CheckConstraint(_KIND_CHECK, name="ck_vat_claimant_documents_kind"),
        sa.CheckConstraint(_COUNTRY_CHECK, name="ck_vat_claimant_documents_country"),
        sa.UniqueConstraint(
            "org_id",
            "entity_id",
            "kind",
            "country",
            "sha256",
            name="uq_vat_claimant_documents_key",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_claimant_documents_entity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_claimant_documents_org_id"),
        "vat_claimant_documents",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_vat_claimant_documents_org_entity",
        "vat_claimant_documents",
        ["org_id", "entity_id"],
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

    op.drop_index("ix_vat_claimant_documents_org_entity", table_name="vat_claimant_documents")
    op.drop_index(op.f("ix_vat_claimant_documents_org_id"), table_name="vat_claimant_documents")
    op.drop_table("vat_claimant_documents")
    op.drop_column("issuer_profiles", "nace_code")
