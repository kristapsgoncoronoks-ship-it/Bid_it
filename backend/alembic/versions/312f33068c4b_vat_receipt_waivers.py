"""vat_receipt_waivers — receipt-control waivers (WO-58, G2.6 slice 3, C9,
R15, ADR-P3 / ADR-0023)

One new tenant table for the transport vertical: `vat_receipt_waivers`. See
`app/models/transport/receipt_waiver.py`'s module docstring for the full
design rationale (why the grain is `(org, claim, supplier)` — narrower than
the harvested `(entity, refund_country, ref_period, supplier)`, since a
claim's own grain already fixes those three fields — and why the FK is
`ondelete=CASCADE`).

This is a new TENANT table — RLS lands in THIS SAME migration (master-context
§4.2: no new tenant table without its RLS policy in the same migration).

`app.services.transport.waiver.set_waiver` is the only writer of a NEW row
(refuses a supplier with any registered invoice for the claim's refund
country); `remove_waiver` is the only function that deletes one.

Revision ID: 312f33068c4b
Revises: bc783e1ec7c2
Create Date: 2026-07-30 02:13:20.345478
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "312f33068c4b"
down_revision: Union[str, None] = "bc783e1ec7c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("vat_receipt_waivers",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "vat_receipt_waivers",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("claim_id", app.models.base.GUID(), nullable=False),
        sa.Column("supplier", sa.String(length=60), nullable=False),
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
        # R15 — a second `set_waiver` call on the same key returns the
        # existing row rather than inserting a duplicate.
        sa.UniqueConstraint(
            "org_id",
            "claim_id",
            "supplier",
            name="uq_vat_receipt_waivers_key",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        # A waiver has no meaning once its claim is gone — CASCADE (mirrors
        # vat_claim_lines.claim_id's own CASCADE FK, WO-49).
        sa.ForeignKeyConstraint(
            ["org_id", "claim_id"],
            ["vat_refund_claims.org_id", "vat_refund_claims.id"],
            name="fk_vat_receipt_waivers_claim",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_receipt_waivers_org_id"), "vat_receipt_waivers", ["org_id"], unique=False
    )
    op.create_index(
        "ix_vat_receipt_waivers_org_claim",
        "vat_receipt_waivers",
        ["org_id", "claim_id"],
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

    op.drop_index("ix_vat_receipt_waivers_org_claim", table_name="vat_receipt_waivers")
    op.drop_index(op.f("ix_vat_receipt_waivers_org_id"), table_name="vat_receipt_waivers")
    op.drop_table("vat_receipt_waivers")
