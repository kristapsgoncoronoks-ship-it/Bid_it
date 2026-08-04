"""fuel_extraction_baselines — the anti-drift extraction baseline
(WO-70, G3.3 slice 2, ADR-P3 / ADR-0023)

One new tenant table for the transport vertical: `fuel_extraction_
baselines` — the known-good per-(statement digest × currency) parsed
aggregates recorded at a statement's first successful registration
(`BA_fleet_fuel.md` §2.1a "Anti-drift"). See `app/models/transport/
extraction_baseline.py`'s module docstring for the full design
rationale (why the key is the content digest, why the figures are
local-currency per-currency, why `line_count` is a third metric).

This is a new TENANT table — RLS lands in THIS SAME migration (master-
context §4.2: no new tenant table without its RLS policy in the same
migration). Deliberately NO composite entity FK: parsing never sees the
entity, so the baseline is a property of (org, bytes) only.

`app.services.transport.extraction_baseline.record` is the only
first-time writer (confirm-time); `rebaseline` the only overwrite;
`remove_baseline` the only deleter — all audited.

Revision ID: c7d2e9a41f58
Revises: b3f1c6d2a904
Create Date: 2026-08-04 21:20:00.000000
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "c7d2e9a41f58"
down_revision: Union[str, None] = "b3f1c6d2a904"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("fuel_extraction_baselines",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "fuel_extraction_baselines",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("statement_sha256", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("network", sa.String(length=60), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("net_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("vat_total", sa.Numeric(14, 2), nullable=False),
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
        sa.CheckConstraint("line_count >= 0", name="ck_fuel_extraction_baselines_lines_nonneg"),
        sa.UniqueConstraint(
            "org_id",
            "statement_sha256",
            "currency",
            name="uq_fuel_extraction_baselines_key",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_fuel_extraction_baselines_org_id"),
        "fuel_extraction_baselines",
        ["org_id"],
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
        op.f("ix_fuel_extraction_baselines_org_id"), table_name="fuel_extraction_baselines"
    )
    op.drop_table("fuel_extraction_baselines")
