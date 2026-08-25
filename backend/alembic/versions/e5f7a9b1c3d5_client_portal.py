"""Client portal: per-customer magic-link tokens, the quote-viewed stamp,
and per-document sharing.

Revision ID: e5f7a9b1c3d5
Revises: c3d5e7f9a1b3
Create Date: 2026-08-25

WO-I (docs/design/crm-module-research.md Part 3). One tenant table
(ENABLE+FORCE RLS here, registry + parity probe in the same commit) and two
columns: project_offers.viewed_at (a stamp, not a stage event — viewing is
information, not movement) and project_documents.shared_with_customer
(sharing is per-document and OFF by default).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision: str = "e5f7a9b1c3d5"
down_revision: Union[str, None] = "c3d5e7f9a1b3"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TENANT_TABLES = ("customer_portal_tokens",)


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "customer_portal_tokens",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id",
            GUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("customer_id", GUID(), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(320), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_customer_portal_tokens_org_id"),
        sa.UniqueConstraint("token", name="uq_customer_portal_tokens_token"),
        sa.ForeignKeyConstraint(
            ["org_id", "customer_id"],
            ["customers.org_id", "customers.id"],
            name="fk_customer_portal_tokens_customer",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_customer_portal_tokens_org_id", "customer_portal_tokens", ["org_id"])
    op.create_index(
        "ix_customer_portal_tokens_org_customer",
        "customer_portal_tokens",
        ["org_id", "customer_id"],
    )

    op.add_column(
        "project_offers", sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "project_documents",
        sa.Column(
            "shared_with_customer", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )

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
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON customer_portal_tokens")
    op.drop_column("project_documents", "shared_with_customer")
    op.drop_column("project_offers", "viewed_at")
    op.drop_index("ix_customer_portal_tokens_org_customer", table_name="customer_portal_tokens")
    op.drop_index("ix_customer_portal_tokens_org_id", table_name="customer_portal_tokens")
    op.drop_table("customer_portal_tokens")
