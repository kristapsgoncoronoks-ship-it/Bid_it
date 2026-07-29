"""vat_note_invoice_overrides — the admin-curated note→invoice override table
(WO-52, G2.4, C3/C4, R16, ADR-P3 / ADR-0023)

One new tenant table for the transport vertical: `vat_note_invoice_overrides`.
See `app/models/transport/note_override.py`'s module docstring for the full
design rationale (why the target is a typed `SET NULL` FK rather than a text
column re-validated on every read, and why `entity_id` widens the harvested
BA key).

This is a new TENANT table — RLS lands in THIS SAME migration (master-context
§4.2: no new tenant table without its RLS policy in the same migration).

No code in this order freezes a `vat_claim_lines` row or runs the future G2.6
gate stack — `app.services.transport.invoice_match.set_note_override` is the
only writer of this table, and `app.services.transport.claim_lines.
build_claim_lines` is the only writer of LIVE (unfrozen) `VatRefundClaimLine`
rows this order populates.

Revision ID: 4cb7fca7e508
Revises: dea0a87e6b0d
Create Date: 2026-07-29 21:57:27.504820
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "4cb7fca7e508"
down_revision: Union[str, None] = "dea0a87e6b0d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("vat_note_invoice_overrides",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "vat_note_invoice_overrides",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("entity_id", app.models.base.GUID(), nullable=False),
        sa.Column("supplier", sa.String(length=60), nullable=False),
        sa.Column("refund_country", sa.String(length=2), nullable=False),
        sa.Column("invoice_ref", sa.String(length=120), nullable=False),
        # Always NOT NULL — see the model docstring "WHY CASCADE...": once
        # the target invoice is de-registered (deleted), this ROW is deleted
        # with it (CASCADE), never left behind with a null target.
        sa.Column("target_invoice_id", app.models.base.GUID(), nullable=False),
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
        # C4/R16 — the override key. A second `set_note_override` call with
        # the same key UPDATES this row's target rather than inserting a
        # duplicate.
        sa.UniqueConstraint(
            "org_id",
            "entity_id",
            "supplier",
            "refund_country",
            "invoice_ref",
            name="uq_vat_note_invoice_overrides_key",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_note_invoice_overrides_entity",
            ondelete="RESTRICT",
        ),
        # CASCADE, not RESTRICT and not SET NULL — de-registering (deleting)
        # the target invoice must never be blocked (rules out RESTRICT), and
        # a composite-FK SET NULL would try to null org_id too (NOT NULL —
        # see the model docstring "WHY CASCADE..." for why that fails).
        # Deleting the target invoice deletes this override row instead,
        # which is exactly R16's "the override silently stops resolving".
        sa.ForeignKeyConstraint(
            ["org_id", "target_invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_vat_note_invoice_overrides_target",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_note_invoice_overrides_org_id"),
        "vat_note_invoice_overrides",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_vat_note_invoice_overrides_org_entity",
        "vat_note_invoice_overrides",
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

    op.drop_index(
        "ix_vat_note_invoice_overrides_org_entity", table_name="vat_note_invoice_overrides"
    )
    op.drop_index(
        op.f("ix_vat_note_invoice_overrides_org_id"), table_name="vat_note_invoice_overrides"
    )
    op.drop_table("vat_note_invoice_overrides")
