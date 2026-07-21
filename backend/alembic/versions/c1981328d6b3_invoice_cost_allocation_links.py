"""invoice cost-allocation links — normalise dimensions to the master tables (Slice 2)

Adds nullable FK columns from `invoices` to the Slice-1 master tables
(`cost_centers`, `departments`, `projects`). Each is a COMPOSITE FK
`(org_id, <dim>_id) → <master>(org_id, id)` so a cross-tenant link is
structurally impossible — the same guard cost_centers uses for its department.

Additive and dual-read: the free-text `cost_center`/`department`/`project` tags
stay as the fallback; a backfill job (`costing.backfill_links`) resolves existing
rows code→id. Deprecating the free-text columns is a later slice. `ON DELETE SET
NULL` is nominal only — master data is archived, never hard-deleted.

Revision ID: c1981328d6b3
Revises: a1c2e3f4b5d6
Create Date: 2026-07-21 11:20:22.733448
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "c1981328d6b3"
down_revision: Union[str, None] = "a1c2e3f4b5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cost_center_id", app.models.base.GUID(), nullable=True))
        batch_op.add_column(sa.Column("department_id", app.models.base.GUID(), nullable=True))
        batch_op.add_column(sa.Column("project_id", app.models.base.GUID(), nullable=True))
        batch_op.create_index(
            "ix_invoices_org_cost_center", ["org_id", "cost_center_id"], unique=False
        )
        batch_op.create_index(
            "ix_invoices_org_department", ["org_id", "department_id"], unique=False
        )
        batch_op.create_index("ix_invoices_org_project", ["org_id", "project_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_invoices_cost_center",
            "cost_centers",
            ["org_id", "cost_center_id"],
            ["org_id", "id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_invoices_department",
            "departments",
            ["org_id", "department_id"],
            ["org_id", "id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_invoices_project",
            "projects",
            ["org_id", "project_id"],
            ["org_id", "id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.drop_constraint("fk_invoices_cost_center", type_="foreignkey")
        batch_op.drop_constraint("fk_invoices_department", type_="foreignkey")
        batch_op.drop_constraint("fk_invoices_project", type_="foreignkey")
        batch_op.drop_index("ix_invoices_org_project")
        batch_op.drop_index("ix_invoices_org_department")
        batch_op.drop_index("ix_invoices_org_cost_center")
        batch_op.drop_column("project_id")
        batch_op.drop_column("department_id")
        batch_op.drop_column("cost_center_id")
