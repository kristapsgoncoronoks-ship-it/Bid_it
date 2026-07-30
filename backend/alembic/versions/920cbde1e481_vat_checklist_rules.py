"""vat_checklist_rules — the adjustable submission checklist as DATA
(WO-60, G2.10 slice 1, §3.E, R45, ADR-P3 / ADR-0023)

One new tenant table for the transport vertical: `vat_checklist_rules`.
See `app/models/transport/checklist_rule.py`'s module docstring for the
full design rationale (why rules are rows, not code, and why only two of
the six harvested defaults are seeded in this slice).

This is a new TENANT table — RLS lands in THIS SAME migration (master-
context §4.2: no new tenant table without its RLS policy in the same
migration).

`app.services.transport.checklist.seed_default_rules` is the only inserter
of a new row; `set_active` is the only function that flips `active`.

Revision ID: 920cbde1e481
Revises: 312f33068c4b
Create Date: 2026-07-30 03:34:39.000121
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "920cbde1e481"
down_revision: Union[str, None] = "312f33068c4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("vat_checklist_rules",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "vat_checklist_rules",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("scope", sa.String(length=12), nullable=False),
        sa.Column("check_type", sa.String(length=12), nullable=False),
        sa.Column("reference", sa.String(length=60), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("sort", sa.Integer(), server_default="0", nullable=False),
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
        # R45 — one rule per (org, key). `seed_default_rules` inserts each
        # DEFAULT_RULES key exactly once; `set_active` never inserts.
        sa.UniqueConstraint("org_id", "key", name="uq_vat_checklist_rules_key"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vat_checklist_rules_org_id"), "vat_checklist_rules", ["org_id"], unique=False
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

    op.drop_index(op.f("ix_vat_checklist_rules_org_id"), table_name="vat_checklist_rules")
    op.drop_table("vat_checklist_rules")
