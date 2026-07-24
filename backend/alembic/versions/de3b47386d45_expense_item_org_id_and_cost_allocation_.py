"""expense_item org_id + cost-allocation links (Slice 2b)

Denormalises `org_id` onto `expense_items` (previously scoped only via its parent
report) so the row is first-class tenant-scoped — the ORM guard + Postgres RLS
restrict it directly — and adds the same composite-FK cost-allocation links
`invoices` got in Slice 2.

`org_id` is added NULLABLE, backfilled from the parent `expense_reports.org_id`,
then set NOT NULL — the additive→backfill→enforce pattern (safe on a populated
table). New rows get `org_id` from the model's before_insert hook.

Revision ID: de3b47386d45
Revises: c1981328d6b3
Create Date: 2026-07-21 11:51:30.429090
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

revision: str = "de3b47386d45"
down_revision: Union[str, None] = "c1981328d6b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("expense_items",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    # 1) Add the new columns. org_id starts NULLABLE so existing rows are valid
    #    until the backfill runs.
    with op.batch_alter_table("expense_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("org_id", app.models.base.GUID(), nullable=True))
        batch_op.add_column(sa.Column("cost_center_id", app.models.base.GUID(), nullable=True))
        batch_op.add_column(sa.Column("department_id", app.models.base.GUID(), nullable=True))
        batch_op.add_column(sa.Column("project_id", app.models.base.GUID(), nullable=True))

    # 2) Backfill org_id from the parent report.
    op.execute(
        "UPDATE expense_items SET org_id = ("
        "SELECT org_id FROM expense_reports WHERE expense_reports.id = expense_items.report_id"
        ") WHERE org_id IS NULL"
    )

    # 3) Enforce NOT NULL, then add the indexes + FKs (org + composite links).
    with op.batch_alter_table("expense_items", schema=None) as batch_op:
        batch_op.alter_column("org_id", existing_type=app.models.base.GUID(), nullable=False)
        batch_op.create_index(batch_op.f("ix_expense_items_org_id"), ["org_id"], unique=False)
        batch_op.create_index(
            "ix_expense_items_org_cost_center", ["org_id", "cost_center_id"], unique=False
        )
        batch_op.create_index(
            "ix_expense_items_org_department", ["org_id", "department_id"], unique=False
        )
        batch_op.create_index(
            "ix_expense_items_org_project", ["org_id", "project_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_expense_items_org", "organizations", ["org_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_foreign_key(
            "fk_expense_items_cost_center",
            "cost_centers",
            ["org_id", "cost_center_id"],
            ["org_id", "id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_expense_items_department",
            "departments",
            ["org_id", "department_id"],
            ["org_id", "id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_expense_items_project",
            "projects",
            ["org_id", "project_id"],
            ["org_id", "id"],
            ondelete="SET NULL",
        )

    # 4) Postgres RLS: expense_items is now a first-class tenant table.
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
    with op.batch_alter_table("expense_items", schema=None) as batch_op:
        batch_op.drop_constraint("fk_expense_items_department", type_="foreignkey")
        batch_op.drop_constraint("fk_expense_items_project", type_="foreignkey")
        batch_op.drop_constraint("fk_expense_items_cost_center", type_="foreignkey")
        batch_op.drop_constraint("fk_expense_items_org", type_="foreignkey")
        batch_op.drop_index("ix_expense_items_org_project")
        batch_op.drop_index("ix_expense_items_org_department")
        batch_op.drop_index("ix_expense_items_org_cost_center")
        batch_op.drop_index(batch_op.f("ix_expense_items_org_id"))
        batch_op.drop_column("project_id")
        batch_op.drop_column("department_id")
        batch_op.drop_column("cost_center_id")
        batch_op.drop_column("org_id")
