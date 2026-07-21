"""data retention policies + legal holds (Phase 4, ADR-0019)

Two tenant-scoped tables:
  * retention_policies — per-(org, category) keep-N-days rule (absence = keep
    forever; retention is opt-in and safe by default).
  * legal_holds — a litigation/audit hold that suspends purging while active.

Both get the Postgres RLS policy (their own TENANT_TABLES, unioned by the RLS
coverage guard).

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-21 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.models.base


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("retention_policies", "legal_holds")

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        'retention_policies',
        sa.Column('id', app.models.base.GUID(), nullable=False),
        sa.Column('org_id', app.models.base.GUID(), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('retain_days', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'category', name='uq_retention_org_category'),
    )
    op.create_index('ix_retention_policies_org_id', 'retention_policies', ['org_id'])

    op.create_table(
        'legal_holds',
        sa.Column('id', app.models.base.GUID(), nullable=False),
        sa.Column('org_id', app.models.base.GUID(), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('placed_by', sa.String(length=255), nullable=True),
        sa.Column('released_by', sa.String(length=255), nullable=True),
        sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_legal_holds_org_id', 'legal_holds', ['org_id'])
    op.create_index('ix_legal_holds_active', 'legal_holds', ['active'])

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

    op.drop_index('ix_legal_holds_active', table_name='legal_holds')
    op.drop_index('ix_legal_holds_org_id', table_name='legal_holds')
    op.drop_table('legal_holds')
    op.drop_index('ix_retention_policies_org_id', table_name='retention_policies')
    op.drop_table('retention_policies')
