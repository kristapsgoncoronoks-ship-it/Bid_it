"""durable job queue

Revision ID: d5e6f7081920
Revises: c4d5e6f70819
Create Date: 2026-07-20 16:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.models.base  # portable GUID type used by every table


revision: str = 'd5e6f7081920'
down_revision: Union[str, None] = 'c4d5e6f70819'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'jobs',
        sa.Column('org_id', app.models.base.GUID(), nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=120), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('run_after', sa.DateTime(timezone=True), nullable=False),
        sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('locked_by', sa.String(length=64), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.Column('id', app.models.base.GUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'kind', 'idempotency_key', name='uq_jobs_idempotency'),
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_jobs_org_id'), ['org_id'], unique=False)
        batch_op.create_index('ix_jobs_ready', ['status', 'run_after'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_jobs_ready')
        batch_op.drop_index(batch_op.f('ix_jobs_org_id'))
    op.drop_table('jobs')
