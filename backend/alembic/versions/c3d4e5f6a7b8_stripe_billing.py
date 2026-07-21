"""stripe billing: org customer/subscription refs + webhook idempotency ledger

Adds `stripe_customer_id` / `stripe_subscription_id` to organizations (the tenant
↔ Stripe linkage; the signed webhook is the authority for plan/status) and a
`processed_stripe_events` table (idempotency ledger for at-least-once webhook
delivery; ADR-0011 / ADR-0013). The events table is NOT tenant-scoped and needs
no RLS policy — it carries no org_id.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-21 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.models.base


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('stripe_customer_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('stripe_subscription_id', sa.String(length=64), nullable=True))
        batch_op.create_index(
            'ix_organizations_stripe_customer_id', ['stripe_customer_id'], unique=True
        )

    op.create_table(
        'processed_stripe_events',
        sa.Column('id', app.models.base.GUID(), nullable=False),
        sa.Column('event_id', sa.String(length=64), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_processed_stripe_events_event_id', 'processed_stripe_events', ['event_id'], unique=True
    )


def downgrade() -> None:
    op.drop_index('ix_processed_stripe_events_event_id', table_name='processed_stripe_events')
    op.drop_table('processed_stripe_events')

    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_index('ix_organizations_stripe_customer_id')
        batch_op.drop_column('stripe_subscription_id')
        batch_op.drop_column('stripe_customer_id')
