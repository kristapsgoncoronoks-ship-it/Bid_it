"""sso_connections: SAML SP config fields (ADR-0021 scaffold)

IdP SSO URL / entity id / signing cert for the SAML SP request side + metadata.
Assertion consumption is deferred to a vetted XML-DSig library + a real IdP.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-21 22:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('sso_connections', schema=None) as batch_op:
        batch_op.add_column(sa.Column('saml_sso_url', sa.String(length=400), nullable=True))
        batch_op.add_column(sa.Column('saml_idp_entity_id', sa.String(length=400), nullable=True))
        batch_op.add_column(sa.Column('saml_idp_cert', sa.String(length=4000), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('sso_connections', schema=None) as batch_op:
        batch_op.drop_column('saml_idp_cert')
        batch_op.drop_column('saml_idp_entity_id')
        batch_op.drop_column('saml_sso_url')
