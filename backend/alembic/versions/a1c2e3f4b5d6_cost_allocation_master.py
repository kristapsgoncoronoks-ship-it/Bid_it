"""cost-allocation master data — departments, cost_centers, projects (Slice 1)

First slice of the normalised logical data model (docs/architecture/data-model.md):
promotes the free-text cost-allocation dimensions to first-class tenant-scoped
master tables. `cost_centers.department_id` uses a COMPOSITE FK
`(org_id, department_id) → departments(org_id, id)` so a cross-tenant reference
is structurally impossible. All three are tenant-scoped → Postgres RLS policies.

Revision ID: a1c2e3f4b5d6
Revises: f2a3b4c5d6e7
Create Date: 2026-07-22 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.models.base


revision: str = 'a1c2e3f4b5d6'
down_revision: Union[str, None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("departments", "cost_centers", "projects")

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)

_TS = dict(server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False)


def _common_cols() -> list:
    return [
        sa.Column('id', app.models.base.GUID(), nullable=False),
        sa.Column('org_id', app.models.base.GUID(), nullable=False),
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='active'),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), **_TS),
        sa.Column('updated_at', sa.DateTime(timezone=True), **_TS),
    ]


def upgrade() -> None:
    op.create_table(
        'departments', *_common_cols(),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'code', name='uq_departments_org_code'),
        sa.UniqueConstraint('org_id', 'id', name='uq_departments_org_id'),
    )
    op.create_index('ix_departments_org_id', 'departments', ['org_id'])
    op.create_index('ix_departments_org_status', 'departments', ['org_id', 'status'])

    op.create_table(
        'cost_centers', *_common_cols(),
        sa.Column('department_id', app.models.base.GUID(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(
            ['org_id', 'department_id'], ['departments.org_id', 'departments.id'],
            name='fk_cost_centers_department', ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'code', name='uq_cost_centers_org_code'),
        sa.UniqueConstraint('org_id', 'id', name='uq_cost_centers_org_id'),
    )
    op.create_index('ix_cost_centers_org_id', 'cost_centers', ['org_id'])
    op.create_index('ix_cost_centers_org_status', 'cost_centers', ['org_id', 'status'])
    op.create_index('ix_cost_centers_org_department', 'cost_centers', ['org_id', 'department_id'])

    op.create_table(
        'projects', *_common_cols(),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'code', name='uq_projects_org_code'),
        sa.UniqueConstraint('org_id', 'id', name='uq_projects_org_id'),
    )
    op.create_index('ix_projects_org_id', 'projects', ['org_id'])
    op.create_index('ix_projects_org_status', 'projects', ['org_id', 'status'])

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
    op.drop_table('projects')
    op.drop_table('cost_centers')
    op.drop_table('departments')
