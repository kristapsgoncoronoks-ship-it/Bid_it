"""vat_claimed_invoices — the one-invoice-one-submission lock table (WO-51, G2.2,
R4/R5, ADR-P3 / ADR-0023)

One new tenant table for the transport vertical: `vat_claimed_invoices`, THE
lock — `UNIQUE(org_id, entity_id, refund_country, supplier, invoice_ref)`. See
`app/models/transport/lock.py`'s module docstring for the full design
rationale (why `entity_id`/`refund_country` are denormalized rather than read
through `claim_id`, why the natural key widens the harvested BA text with
`org_id`, and what each of the three composite FKs protects).

This is a new TENANT table — RLS lands in THIS SAME migration (master-context
§4.2: no new tenant table without its RLS policy in the same migration).

No code in this order populates a `vat_claim_lines` row or runs the future
G2.6 gate stack — `app.services.transport.lock.submit_claim` is a minimal
stub status transition (draft -> submitted) that exists only to prove the
locking primitive.

Revision ID: dea0a87e6b0d
Revises: fc45baaf3283
Create Date: 2026-07-29 06:48:19.198056
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "dea0a87e6b0d"
down_revision: Union[str, None] = "fc45baaf3283"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("vat_claimed_invoices",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "vat_claimed_invoices",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("claim_id", app.models.base.GUID(), nullable=False),
        sa.Column("entity_id", app.models.base.GUID(), nullable=False),
        sa.Column("refund_country", sa.String(length=2), nullable=False),
        sa.Column("supplier", sa.String(length=60), nullable=False),
        sa.Column("invoice_ref", sa.String(length=120), nullable=False),
        sa.Column("fuel_transaction_id", app.models.base.GUID(), nullable=False),
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
        # R4 — THE LOCK. A lost race on this constraint raises IntegrityError;
        # a caller must go through app.services.transport.lock.submit_claim,
        # which acquires it via a plain INSERT (never an upsert) in the same
        # flush as the claim's status mutation.
        sa.UniqueConstraint(
            "org_id",
            "entity_id",
            "refund_country",
            "supplier",
            "invoice_ref",
            name="uq_vat_claimed_invoices_lock",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        # Which claim currently holds this lock — mirrors vat_claim_lines.claim_id
        # (WO-49); moot in practice, since no code path deletes a claim.
        sa.ForeignKeyConstraint(
            ["org_id", "claim_id"],
            ["vat_refund_claims.org_id", "vat_refund_claims.id"],
            name="fk_vat_claimed_invoices_claim",
            ondelete="CASCADE",
        ),
        # Matches every other transport table's entity FK (WO-49/WO-50): an
        # org's own legal entity must not be deletable out from under a live lock.
        sa.ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_claimed_invoices_entity",
            ondelete="RESTRICT",
        ),
        # RESTRICT, not CASCADE — a fuel transaction with an active lock must
        # not be deletable. WO-50 built the composite (org_id, id) unique
        # constraint on fuel_transactions specifically for this FK target.
        sa.ForeignKeyConstraint(
            ["org_id", "fuel_transaction_id"],
            ["fuel_transactions.org_id", "fuel_transactions.id"],
            name="fk_vat_claimed_invoices_fuel_transaction",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_vat_claimed_invoices_org_id"), "vat_claimed_invoices", ["org_id"], unique=False)
    op.create_index(
        "ix_vat_claimed_invoices_org_claim", "vat_claimed_invoices", ["org_id", "claim_id"], unique=False
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

    op.drop_index("ix_vat_claimed_invoices_org_claim", table_name="vat_claimed_invoices")
    op.drop_index(op.f("ix_vat_claimed_invoices_org_id"), table_name="vat_claimed_invoices")
    op.drop_table("vat_claimed_invoices")
