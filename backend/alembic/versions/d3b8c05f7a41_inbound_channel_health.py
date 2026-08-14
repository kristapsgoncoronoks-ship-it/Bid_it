"""inbound-channel health (H-2) — is the pipe still open, and how would we know?

Revision ID: d3b8c05f7a41
Revises: c7e1a94d5b02
Create Date: 2026-08-14

An inbound channel can die completely while every dashboard stays green,
because success is inferred from a document count — so "nothing arrived" and
"nothing could get through" produce identical silence. This table records the
outcome of each delivery ATTEMPT independently of whether documents came out of
it, which is the only thing that can separate the two.

`expected_cadence_days` is deliberately nullable with NO default. Staleness only
means something against an expected rhythm, and one customer's fortnight of quiet
is another's outage. Until someone states a cadence, the health view reports the
elapsed time as a fact and declines to call the channel broken.
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa

from alembic import op

revision: str = "d3b8c05f7a41"
down_revision: Union[str, None] = "c7e1a94d5b02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("inbound_channel_health",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        "inbound_channel_health",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("channel", sa.String(length=24), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_error_kind", sa.String(length=32), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_detail", sa.Text(), nullable=True),
        sa.Column("expected_cadence_days", sa.Integer(), nullable=True),
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
        sa.UniqueConstraint("org_id", "channel", name="uq_inbound_channel_health_org_channel"),
    )
    op.create_index(
        op.f("ix_inbound_channel_health_org_id"),
        "inbound_channel_health",
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
    op.drop_index(op.f("ix_inbound_channel_health_org_id"), table_name="inbound_channel_health")
    op.drop_table("inbound_channel_health")
