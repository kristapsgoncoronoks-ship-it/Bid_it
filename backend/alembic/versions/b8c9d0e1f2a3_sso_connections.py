"""sso_connections — per-tenant single-sign-on config (ADR-0021)

Tenant-scoped OIDC (implemented) / SAML (scaffold) connection, keyed by a public
`slug` used in the login URL. Gets the Postgres RLS policy (its own TENANT_TABLES,
unioned by the RLS coverage guard).

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-21 20:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.models.base


revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("sso_connections",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.create_table(
        'sso_connections',
        sa.Column('id', app.models.base.GUID(), nullable=False),
        sa.Column('org_id', app.models.base.GUID(), nullable=False),
        sa.Column('slug', sa.String(length=64), nullable=False),
        sa.Column('protocol', sa.String(length=10), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('issuer', sa.String(length=400), nullable=True),
        sa.Column('client_id', sa.String(length=255), nullable=True),
        sa.Column('client_secret', sa.String(length=512), nullable=True),
        sa.Column('allowed_domain', sa.String(length=255), nullable=True),
        sa.Column('jit_enabled', sa.Boolean(), nullable=False),
        sa.Column('default_role', sa.String(length=20), nullable=False),
        sa.Column('saml_metadata_url', sa.String(length=400), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sso_connections_org_id', 'sso_connections', ['org_id'])
    op.create_index('ix_sso_connections_slug', 'sso_connections', ['slug'], unique=True)

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
    op.drop_index('ix_sso_connections_slug', table_name='sso_connections')
    op.drop_index('ix_sso_connections_org_id', table_name='sso_connections')
    op.drop_table('sso_connections')
