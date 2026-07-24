"""issuer registry: multiple legal entities per org (per-entity numbering series)

Turns the singleton issuer_profiles (one per org) into a registry: adds a name,
an is_default flag and payment_instructions, drops the one-per-org UNIQUE on
org_id (keeping a plain index), and links issued_invoices to the issuing entity.
Existing rows backfill to is_default=1 so a single-entity org is unchanged.

Revision ID: d9a1c3e5b7f2
Revises: c7e9f1b3d5a8
Create Date: 2026-07-23 19:05:00.000000
"""

from typing import Sequence, Union

import app.models.base
import sqlalchemy as sa
from alembic import op

revision: str = "d9a1c3e5b7f2"
down_revision: Union[str, None] = "c7e9f1b3d5a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_G = app.models.base.GUID


def upgrade() -> None:
    with op.batch_alter_table("issuer_profiles", schema=None) as b:
        b.add_column(sa.Column("name", sa.String(length=200), nullable=False, server_default=""))
        b.add_column(
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0")
        )
        b.add_column(sa.Column("payment_instructions", sa.Text(), nullable=True))
        # Drop the one-per-org UNIQUE index and re-create it non-unique so an org can
        # register multiple issuer entities.
        b.drop_index("ix_issuer_profiles_org_id")
        b.create_index("ix_issuer_profiles_org_id", ["org_id"], unique=False)
        b.create_unique_constraint("uq_issuer_profiles_org_id_id", ["org_id", "id"])

    # Every existing (singleton) issuer becomes its org's default.
    op.execute("UPDATE issuer_profiles SET is_default = 1")

    with op.batch_alter_table("issued_invoices", schema=None) as b:
        b.add_column(sa.Column("issuer_id", _G(), nullable=True))
        b.create_index("ix_issued_invoices_issuer_id", ["issuer_id"], unique=False)
        b.create_foreign_key(
            "fk_issued_invoices_issuer",
            "issuer_profiles",
            ["issuer_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("issued_invoices", schema=None) as b:
        b.drop_constraint("fk_issued_invoices_issuer", type_="foreignkey")
        b.drop_index("ix_issued_invoices_issuer_id")
        b.drop_column("issuer_id")

    with op.batch_alter_table("issuer_profiles", schema=None) as b:
        b.drop_constraint("uq_issuer_profiles_org_id_id", type_="unique")
        b.drop_index("ix_issuer_profiles_org_id")
        b.create_index("ix_issuer_profiles_org_id", ["org_id"], unique=True)
        b.drop_column("payment_instructions")
        b.drop_column("is_default")
        b.drop_column("name")
