"""everypay billing: org token/renewal columns + billing_payments (redirect flow)

Adds EveryPay support alongside Stripe (ADR-0013):
  * organizations.everypay_token / everypay_next_charge — the stored card token
    (MIT recurring) + next scheduled charge date.
  * billing_payments — a redirect-flow payment attempt awaiting server-side
    confirmation (Stripe needs no such row; it uses subscription webhooks).

`billing_payments` is tenant-scoped, so it gets the same Postgres RLS policy as
the Phase-2 tables. Its name is listed in this migration's own `TENANT_TABLES`
so the RLS coverage guard (test_rls) — which unions the list across migrations —
sees a policy for it.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-21 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.models.base


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tenant-scoped tables introduced by THIS migration (RLS coverage guard unions
# these across all migrations — see tests/test_rls.py).
TENANT_TABLES = ("billing_payments",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('everypay_token', sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column('everypay_next_charge', sa.Date(), nullable=True))

    op.create_table(
        'billing_payments',
        sa.Column('id', app.models.base.GUID(), nullable=False),
        sa.Column('org_id', app.models.base.GUID(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('reference', sa.String(length=64), nullable=False),
        sa.Column('order_reference', sa.String(length=64), nullable=False),
        sa.Column('plan_key', sa.String(length=20), nullable=False),
        sa.Column('amount_eur', sa.Float(), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_billing_payments_org_id', 'billing_payments', ['org_id'])
    op.create_index('ix_billing_payments_reference', 'billing_payments', ['reference'], unique=True)

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

    op.drop_index('ix_billing_payments_reference', table_name='billing_payments')
    op.drop_index('ix_billing_payments_org_id', table_name='billing_payments')
    op.drop_table('billing_payments')

    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_column('everypay_next_charge')
        batch_op.drop_column('everypay_token')
