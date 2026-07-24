"""usage counters and outbound webhooks

Revision ID: f708192a3b4c
Revises: e6f708192a3b
Create Date: 2026-07-20 17:50:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.models.base  # portable GUID type used by every table


revision: str = 'f708192a3b4c'
down_revision: Union[str, None] = 'e6f708192a3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- usage counters -------------------------------------------------------
    op.create_table(
        'usage_counters',
        sa.Column('org_id', app.models.base.GUID(), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('metric', sa.String(length=40), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('id', app.models.base.GUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'period', 'metric', name='uq_usage_org_period_metric'),
    )
    with op.batch_alter_table('usage_counters', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_usage_counters_org_id'), ['org_id'], unique=False)

    # --- webhook endpoints ----------------------------------------------------
    op.create_table(
        'webhook_endpoints',
        sa.Column('org_id', app.models.base.GUID(), nullable=False),
        sa.Column('url', sa.String(length=500), nullable=False),
        sa.Column('secret', sa.String(length=80), nullable=False),
        sa.Column('events', sa.String(length=500), nullable=False),
        sa.Column('description', sa.String(length=200), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('id', app.models.base.GUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('webhook_endpoints', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_webhook_endpoints_org_id'), ['org_id'], unique=False)

    # --- webhook deliveries ---------------------------------------------------
    op.create_table(
        'webhook_deliveries',
        sa.Column('org_id', app.models.base.GUID(), nullable=False),
        sa.Column('endpoint_id', app.models.base.GUID(), nullable=False),
        sa.Column('event_type', sa.String(length=60), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('response_code', sa.Integer(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', app.models.base.GUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['endpoint_id'], ['webhook_endpoints.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('webhook_deliveries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_webhook_deliveries_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_webhook_deliveries_endpoint_id'), ['endpoint_id'], unique=False)
        batch_op.create_index('ix_webhook_deliv_endpoint', ['endpoint_id', 'created_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('webhook_deliveries', schema=None) as batch_op:
        batch_op.drop_index('ix_webhook_deliv_endpoint')
        batch_op.drop_index(batch_op.f('ix_webhook_deliveries_endpoint_id'))
        batch_op.drop_index(batch_op.f('ix_webhook_deliveries_org_id'))
    op.drop_table('webhook_deliveries')

    with op.batch_alter_table('webhook_endpoints', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_webhook_endpoints_org_id'))
    op.drop_table('webhook_endpoints')

    with op.batch_alter_table('usage_counters', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_usage_counters_org_id'))
    op.drop_table('usage_counters')
