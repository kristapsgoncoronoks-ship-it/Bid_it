"""document versions — supersession chain for single-file slots (Slice 5g)

Append-only history for the two document owners that hold exactly one current
file and silently replace it on re-upload: the issuer logo and an expense item's
receipt. The slot is the polymorphic `(owner_type, owner_id)` pair (NOT a
composite FK — the owner is one of two different parent tables), and each upload
appends a row with a monotonic per-slot `version`, one flagged `is_current`.

Backfills a version-1 row for every logo/receipt that already exists, so the
history is truthful for pre-existing data (uploader unknown → NULL).

Revision ID: efd78f4cbe2e
Revises: 98d1896eeb26
Create Date: 2026-07-21 15:40:37.964735
"""

import uuid
from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "efd78f4cbe2e"
down_revision: Union[str, None] = "98d1896eeb26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("document_versions",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)

# Slot kinds — kept in sync with app.models.document_version.
_OWNER_ISSUER_LOGO = "issuer_logo"
_OWNER_EXPENSE_RECEIPT = "expense_item_receipt"


def upgrade() -> None:
    op.create_table(
        "document_versions",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("owner_type", sa.String(length=24), nullable=False),
        sa.Column("owner_id", app.models.base.GUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("mime", sa.String(length=80), nullable=True),
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
        sa.UniqueConstraint(
            "org_id", "owner_type", "owner_id", "version", name="uq_document_versions_slot_version"
        ),
    )
    op.create_index(
        op.f("ix_document_versions_org_id"), "document_versions", ["org_id"], unique=False
    )
    op.create_index(
        "ix_document_versions_slot",
        "document_versions",
        ["org_id", "owner_type", "owner_id"],
        unique=False,
    )

    _backfill(op.get_bind())

    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
            )


def _backfill(bind) -> None:
    """Seed one current version per already-stored logo/receipt."""
    dv = sa.table(
        "document_versions",
        sa.column("id", app.models.base.GUID()),
        sa.column("org_id", app.models.base.GUID()),
        sa.column("owner_type", sa.String()),
        sa.column("owner_id", app.models.base.GUID()),
        sa.column("version", sa.Integer()),
        sa.column("is_current", sa.Boolean()),
        sa.column("sha256", sa.String()),
        sa.column("size", sa.Integer()),
        sa.column("mime", sa.String()),
        sa.column("filename", sa.String()),
        sa.column("uploaded_by", sa.String()),
    )
    rows = []
    for owner_type, query in (
        (
            _OWNER_ISSUER_LOGO,
            "SELECT org_id, id, logo_sha256, logo_size, logo_mime "
            "FROM issuer_profiles WHERE logo_sha256 IS NOT NULL",
        ),
        (
            _OWNER_EXPENSE_RECEIPT,
            "SELECT org_id, id, receipt_sha256, receipt_size, receipt_mime "
            "FROM expense_items WHERE receipt_sha256 IS NOT NULL",
        ),
    ):
        for org_id, owner_id, sha, size, mime in bind.execute(sa.text(query)):
            rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "org_id": org_id,
                    "owner_type": owner_type,
                    "owner_id": owner_id,
                    "version": 1,
                    "is_current": True,
                    "sha256": sha,
                    "size": size if size is not None else 0,
                    "mime": mime,
                    "filename": None,
                    "uploaded_by": None,
                }
            )
    if rows:
        op.bulk_insert(dv, rows)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_index("ix_document_versions_slot", table_name="document_versions")
    op.drop_index(op.f("ix_document_versions_org_id"), table_name="document_versions")
    op.drop_table("document_versions")
