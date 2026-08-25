"""CRM light: customer notes + offer stage events + customers.lifecycle

Revision ID: c3d5e7f9a1b3
Revises: b2c4d6e8f0a2
Create Date: 2026-08-25

WO-H (docs/design/crm-module-research.md). Two small tenant tables, both
with ENABLE+FORCE RLS here and registry + parity probes in the same commit,
plus the lifecycle stage as a COLUMN on customers (prospect|active|dormant|
lost — no lead entity, by documented anti-pattern ruling).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision: str = "c3d5e7f9a1b3"
down_revision: Union[str, None] = "b2c4d6e8f0a2"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TENANT_TABLES = ("customer_notes", "offer_stage_events")
_LIFECYCLE_CHECK = "lifecycle IN ('prospect', 'active', 'dormant', 'lost')"


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "customer_notes",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id",
            GUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("customer_id", GUID(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(320), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_customer_notes_org_id"),
        sa.ForeignKeyConstraint(
            ["org_id", "customer_id"],
            ["customers.org_id", "customers.id"],
            name="fk_customer_notes_customer",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_customer_notes_org_id", "customer_notes", ["org_id"])
    op.create_index(
        "ix_customer_notes_org_customer", "customer_notes", ["org_id", "customer_id"]
    )

    op.create_table(
        "offer_stage_events",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id",
            GUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("offer_id", GUID(), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(320), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_offer_stage_events_org_id"),
        sa.ForeignKeyConstraint(
            ["org_id", "offer_id"],
            ["project_offers.org_id", "project_offers.id"],
            name="fk_offer_stage_events_offer",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_offer_stage_events_org_id", "offer_stage_events", ["org_id"])
    op.create_index(
        "ix_offer_stage_events_org_offer", "offer_stage_events", ["org_id", "offer_id"]
    )

    op.add_column(
        "customers",
        sa.Column("lifecycle", sa.String(16), nullable=False, server_default="active"),
    )
    if bind.dialect.name == "postgresql":
        op.execute(
            f"ALTER TABLE customers ADD CONSTRAINT ck_customers_lifecycle CHECK ({_LIFECYCLE_CHECK})"
        )
    else:
        with op.batch_alter_table("customers") as batch:
            batch.create_check_constraint("ck_customers_lifecycle", _LIFECYCLE_CHECK)

    if bind.dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                f"USING (current_setting('app.current_org', true) IS NULL OR org_id::text = current_setting('app.current_org', true)) WITH CHECK (current_setting('app.current_org', true) IS NULL OR org_id::text = current_setting('app.current_org', true))"
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute("ALTER TABLE customers DROP CONSTRAINT ck_customers_lifecycle")
    else:
        with op.batch_alter_table("customers") as batch:
            batch.drop_constraint("ck_customers_lifecycle", type_="check")
    op.drop_column("customers", "lifecycle")
    op.drop_index("ix_offer_stage_events_org_offer", table_name="offer_stage_events")
    op.drop_index("ix_offer_stage_events_org_id", table_name="offer_stage_events")
    op.drop_table("offer_stage_events")
    op.drop_index("ix_customer_notes_org_customer", table_name="customer_notes")
    op.drop_index("ix_customer_notes_org_id", table_name="customer_notes")
    op.drop_table("customer_notes")
