"""credit notes and recurring invoices

Revision ID: c4d5e6f70819
Revises: ee6f191d4b4f
Create Date: 2026-07-20 16:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.models.base  # portable GUID type used by every table


revision: str = 'c4d5e6f70819'
down_revision: Union[str, None] = 'ee6f191d4b4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- credit-note fields on issued invoices --------------------------------
    with op.batch_alter_table('issued_invoices', schema=None) as batch_op:
        # server_default backfills every existing row as a normal invoice.
        batch_op.add_column(sa.Column('doc_type', sa.String(length=12), nullable=False, server_default='invoice'))
        batch_op.add_column(sa.Column('corrected_invoice_id', app.models.base.GUID(), nullable=True))
        batch_op.add_column(sa.Column('credited_total', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0'))
        batch_op.create_index(batch_op.f('ix_issued_invoices_corrected_invoice_id'), ['corrected_invoice_id'], unique=False)
        batch_op.create_foreign_key('fk_issued_corrected', 'issued_invoices', ['corrected_invoice_id'], ['id'], ondelete='SET NULL')

    # --- credit-note numbering series on the issuer profile -------------------
    with op.batch_alter_table('issuer_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('credit_note_prefix', sa.String(length=16), nullable=False, server_default='CN-'))
        batch_op.add_column(sa.Column('next_credit_number', sa.Integer(), nullable=False, server_default='1'))

    # --- recurring-invoice schedules ------------------------------------------
    op.create_table(
        'recurring_invoices',
        sa.Column('org_id', app.models.base.GUID(), nullable=False),
        sa.Column('partner_id', app.models.base.GUID(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('template_json', sa.Text(), nullable=False),
        sa.Column('frequency', sa.String(length=10), nullable=False),
        sa.Column('interval', sa.Integer(), nullable=False),
        sa.Column('payment_terms_days', sa.Integer(), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('next_run_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('last_generated_at', sa.Date(), nullable=True),
        sa.Column('generated_count', sa.Integer(), nullable=False),
        sa.Column('id', app.models.base.GUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['partner_id'], ['partners.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('recurring_invoices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_recurring_invoices_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_recurring_invoices_partner_id'), ['partner_id'], unique=False)
        batch_op.create_index('ix_recurring_org_due', ['org_id', 'active', 'next_run_date'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('recurring_invoices', schema=None) as batch_op:
        batch_op.drop_index('ix_recurring_org_due')
        batch_op.drop_index(batch_op.f('ix_recurring_invoices_partner_id'))
        batch_op.drop_index(batch_op.f('ix_recurring_invoices_org_id'))
    op.drop_table('recurring_invoices')

    with op.batch_alter_table('issuer_profiles', schema=None) as batch_op:
        batch_op.drop_column('next_credit_number')
        batch_op.drop_column('credit_note_prefix')

    with op.batch_alter_table('issued_invoices', schema=None) as batch_op:
        batch_op.drop_constraint('fk_issued_corrected', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_issued_invoices_corrected_invoice_id'))
        batch_op.drop_column('credited_total')
        batch_op.drop_column('corrected_invoice_id')
        batch_op.drop_column('doc_type')
