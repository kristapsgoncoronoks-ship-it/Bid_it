"""document registry — metadata catalog of stored originals (Slice 5d)

One row per distinct (org, content, kind) stored object, written at the storage
choke point. Additive, tenant-scoped → Postgres RLS + the ORM guard.

Revision ID: 9f4990ff8f69
Revises: 8c5ead96a8e4
Create Date: 2026-07-21 14:46:50.640762
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "9f4990ff8f69"
down_revision: Union[str, None] = "8c5ead96a8e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("documents",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("mime", sa.String(length=80), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("uploaded_by", sa.String(length=200), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "sha256", "kind", name="uq_documents_org_sha_kind"),
    )
    op.create_index("ix_documents_org_created", "documents", ["org_id", "created_at"], unique=False)
    op.create_index(op.f("ix_documents_org_id"), "documents", ["org_id"], unique=False)
    op.create_index("ix_documents_org_kind", "documents", ["org_id", "kind"], unique=False)

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
    op.drop_index("ix_documents_org_kind", table_name="documents")
    op.drop_index(op.f("ix_documents_org_id"), table_name="documents")
    op.drop_index("ix_documents_org_created", table_name="documents")
    op.drop_table("documents")
