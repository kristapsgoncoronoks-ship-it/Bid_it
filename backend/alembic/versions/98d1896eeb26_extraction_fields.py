"""extraction fields — per-field capture provenance (Slice 5f)

Deepens the extraction run into one row per top-level field (status + value +
confidence slot). Linked by a COMPOSITE FK `(org_id, extraction_run_id) →
extraction_runs(org_id, id)` — tenant-safe — so the unique constraint on the
parent run is created FIRST.

Revision ID: 98d1896eeb26
Revises: 9f4990ff8f69
Create Date: 2026-07-21 15:25:01.224075
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "98d1896eeb26"
down_revision: Union[str, None] = "9f4990ff8f69"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("extraction_fields",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    # 1) The composite-FK target must be unique BEFORE the FK references it.
    with op.batch_alter_table("extraction_runs", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_extraction_runs_org_id", ["org_id", "id"])

    # 2) The per-field provenance table.
    op.create_table(
        "extraction_fields",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("extraction_run_id", app.models.base.GUID(), nullable=False),
        sa.Column("field", sa.String(length=40), nullable=False),
        sa.Column("value", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["org_id", "extraction_run_id"],
            ["extraction_runs.org_id", "extraction_runs.id"],
            name="fk_extraction_fields_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_extraction_fields_org_id"), "extraction_fields", ["org_id"], unique=False
    )
    op.create_index(
        "ix_extraction_fields_org_run",
        "extraction_fields",
        ["org_id", "extraction_run_id"],
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
    op.drop_index("ix_extraction_fields_org_run", table_name="extraction_fields")
    op.drop_index(op.f("ix_extraction_fields_org_id"), table_name="extraction_fields")
    op.drop_table("extraction_fields")
    with op.batch_alter_table("extraction_runs", schema=None) as batch_op:
        batch_op.drop_constraint("uq_extraction_runs_org_id", type_="unique")
