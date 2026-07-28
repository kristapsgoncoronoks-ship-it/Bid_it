"""capture field memory — E1.5 extraction learning loop

One row per (org_id, vendor_name, field): the per-vendor x field correction
history that feeds an ADVISORY hint (never an auto-apply) into the next
capture from the same vendor. `vendor_name` is the raw, exact-stripped
captured header vendor_name — the same key `vendors.get_or_create_vendor`
matches a vendor on. No FK to extraction_runs: this table outlives any single
run, accumulating across every capture from a vendor.

Revision ID: a3f556aac756
Revises: 07a09c738fab
Create Date: 2026-07-28 15:22:11.379950
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "a3f556aac756"
down_revision: Union[str, None] = "07a09c738fab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("capture_field_memory",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "capture_field_memory",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("vendor_name", sa.String(length=255), nullable=False),
        sa.Column("field", sa.String(length=40), nullable=False),
        sa.Column("observed_count", sa.Integer(), nullable=False),
        sa.Column("corrected_count", sa.Integer(), nullable=False),
        sa.Column("last_original_value", sa.String(length=500), nullable=True),
        sa.Column("last_corrected_value", sa.String(length=500), nullable=True),
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
        sa.UniqueConstraint("org_id", "vendor_name", "field", name="uq_capture_field_memory_key"),
    )
    op.create_index(
        op.f("ix_capture_field_memory_org_id"), "capture_field_memory", ["org_id"], unique=False
    )
    op.create_index(
        "ix_capture_field_memory_org_vendor",
        "capture_field_memory",
        ["org_id", "vendor_name"],
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
    op.drop_index("ix_capture_field_memory_org_vendor", table_name="capture_field_memory")
    op.drop_index(op.f("ix_capture_field_memory_org_id"), table_name="capture_field_memory")
    op.drop_table("capture_field_memory")
