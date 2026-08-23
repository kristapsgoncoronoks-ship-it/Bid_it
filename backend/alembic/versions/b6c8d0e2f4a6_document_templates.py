"""dynamic document templates — platform masters + per-client saved versions

Revision ID: b6c8d0e2f4a6
Revises: a5b7c9d1e3f5
Create Date: 2026-08-16

Owner direction: the server owner maintains master templates (demo texts ship
now; the lawyer's standardized texts replace the bodies later through the same
surface); each client adjusts and saves as many versions as they like.
`org_templates` is a tenant table: registered, probed, ENABLE+FORCE RLS here.
`platform_templates` is org-less reference material (the ecb_rates pattern).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision: str = "b6c8d0e2f4a6"
down_revision: Union[str, None] = "a5b7c9d1e3f5"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

TENANT_TABLES = ("org_templates",)


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "platform_templates",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("key", name="uq_platform_templates_key"),
    )

    op.create_table(
        "org_templates",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id",
            GUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_platform_id", GUID(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(320), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_org_templates_org_id"),
    )
    op.create_index("ix_org_templates_org_kind", "org_templates", ["org_id", "kind"])
    op.create_index("ix_org_templates_org_id", "org_templates", ["org_id"])

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
    op.drop_index("ix_org_templates_org_id", table_name="org_templates")
    op.drop_index("ix_org_templates_org_kind", table_name="org_templates")
    op.drop_table("org_templates")
    op.drop_table("platform_templates")
