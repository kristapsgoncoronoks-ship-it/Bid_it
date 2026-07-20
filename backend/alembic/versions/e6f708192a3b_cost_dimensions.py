"""cost-allocation dimensions on invoices and expense items

Revision ID: e6f708192a3b
Revises: d5e6f7081920
Create Date: 2026-07-20 17:15:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e6f708192a3b'
down_revision: Union[str, None] = 'd5e6f7081920'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DIMENSIONS = ("cost_center", "department", "project", "vehicle", "property_ref")


def upgrade() -> None:
    for table in ("invoices", "expense_items"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            for dim in _DIMENSIONS:
                batch_op.add_column(sa.Column(dim, sa.String(length=80), nullable=True))
                batch_op.create_index(batch_op.f(f"ix_{table}_{dim}"), [dim], unique=False)


def downgrade() -> None:
    for table in ("invoices", "expense_items"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            for dim in _DIMENSIONS:
                batch_op.drop_index(batch_op.f(f"ix_{table}_{dim}"))
                batch_op.drop_column(dim)
