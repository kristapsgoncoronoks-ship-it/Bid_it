"""vat_supplier_cadences + vat_receipt_controls — receipt control
(WO-72, G3.5, ADR-P3 / ADR-0023)

Two new tenant tables for the transport vertical (`BA_fleet_fuel.md`
§3.J): `vat_supplier_cadences` — the admin cadence assignment per
free-text supplier code (the WO-61 registry precedent; beats the
harvested `DEFAULT_CADENCES`) — and `vat_receipt_controls` — the
persisted cadence × activity expectation grid ("engine-owned
`invoice_receipt_control`", §3.J item 4), one row per slot, whose
override columns (`waived`/`note`) survive re-runs because only
`receipt_control.set_control_override` ever writes them. See
`app/models/transport/receipt_control.py`'s module docstring for the
full design rationale (slot grain, `country=""` never NULL, the four
statuses, the boundary vs WO-58's `vat_receipt_waivers`).

New TENANT tables — RLS lands in THIS SAME migration (master-context
§4.2: no new tenant table without its RLS policy in the same migration).

Revision ID: d8e4f2a61b37
Revises: c7d2e9a41f58
Create Date: 2026-08-05 10:00:00.000000
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "d8e4f2a61b37"
down_revision: Union[str, None] = "c7d2e9a41f58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("vat_supplier_cadences", "vat_receipt_controls")

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)

_CADENCE_CHECK = "cadence IN ('semi-monthly', 'monthly', 'monthly-per-country')"
_STATUS_CHECK = "status IN ('no_activity', 'received_doc', 'received_no_doc', 'missing')"
_SLOT_CHECK = "slot IN ('M', 'H1', 'H2')"


def upgrade() -> None:
    op.create_table(
        "vat_supplier_cadences",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("supplier", sa.String(length=60), nullable=False),
        sa.Column("cadence", sa.String(length=20), nullable=False),
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
        sa.CheckConstraint(_CADENCE_CHECK, name="ck_vat_supplier_cadences_cadence"),
        sa.UniqueConstraint("org_id", "supplier", name="uq_vat_supplier_cadences_key"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_supplier_cadences_org_id"), "vat_supplier_cadences", ["org_id"], unique=False
    )

    op.create_table(
        "vat_receipt_controls",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("entity_id", app.models.base.GUID(), nullable=False),
        sa.Column("supplier", sa.String(length=60), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("slot", sa.String(length=2), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("txn_count", sa.Integer(), nullable=False),
        sa.Column("waived", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint(_STATUS_CHECK, name="ck_vat_receipt_controls_status"),
        sa.CheckConstraint(_SLOT_CHECK, name="ck_vat_receipt_controls_slot"),
        sa.CheckConstraint("txn_count >= 0", name="ck_vat_receipt_controls_txns_nonneg"),
        sa.UniqueConstraint(
            "org_id",
            "entity_id",
            "supplier",
            "period",
            "slot",
            "country",
            name="uq_vat_receipt_controls_key",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_receipt_controls_entity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_receipt_controls_org_id"), "vat_receipt_controls", ["org_id"], unique=False
    )
    op.create_index(
        "ix_vat_receipt_controls_org_period",
        "vat_receipt_controls",
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

    op.drop_index("ix_vat_receipt_controls_org_period", table_name="vat_receipt_controls")
    op.drop_index(op.f("ix_vat_receipt_controls_org_id"), table_name="vat_receipt_controls")
    op.drop_table("vat_receipt_controls")
    op.drop_index(op.f("ix_vat_supplier_cadences_org_id"), table_name="vat_supplier_cadences")
    op.drop_table("vat_supplier_cadences")
