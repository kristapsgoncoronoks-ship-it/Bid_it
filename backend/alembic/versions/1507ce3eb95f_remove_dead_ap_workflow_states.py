"""remove dead ap workflow states (C1.9)

`uploaded`, `processing`, `review_required` were declared in
`invoice_workflow_state` (migration `e7b9c1d3f5a2`) and wired into the
transition graph (`app/services/invoice_workflow.py`) but were never assigned
to any invoice: every `Invoice(...)` construction site (`create_invoice`,
`seed.py`) leaves `workflow_state` at its column default, `draft`. Recon for
work order WO-25 (docs/plan/plan-a/wo/WO-25-C19.md) confirmed this by grep
across `app/`, `tests/`, and the frontend — the three names appear nowhere as
an assignment target.

Postgres has no `ALTER TYPE ... DROP VALUE`, so dropping the three labels
means recreating the native enum type without them and repointing the
column. A pre-flight guard refuses to run if any row is ever found holding
one of the three values (belt-and-braces: the whole premise of this
migration is that the count is provably zero).

SQLite is unaffected here: the test suite builds its schema via
`Base.metadata.create_all` (see `tests/conftest.py`), not via these
migrations, so the model change alone (removing the three `WorkflowState`
members) is what narrows its CHECK constraint on a fresh test DB. This
migration only needs to touch Postgres, matching the `is_pg`-guarded pattern
already used in `e7b9c1d3f5a2_invoice_review_approval.py`.

Revision ID: 1507ce3eb95f
Revises: b45eaac2f3f7
Create Date: 2026-07-27 15:09:17.206122
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1507ce3eb95f'
down_revision: Union[str, None] = 'b45eaac2f3f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEAD_VALUES = ("uploaded", "processing", "review_required")

_LIVE_VALUES = (
    "draft", "submitted", "partially_approved", "approved", "rejected",
    "scheduled_for_payment", "partially_paid", "paid", "disputed",
    "cancelled", "archived",
)

_ENUM_NAME = "invoice_workflow_state"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite has no native enum type here (create_all rebuilds the CHECK
        # constraint straight from the narrowed model); nothing to migrate.
        return

    # Defensive guard: this migration's whole premise is that the three
    # states are dead. Refuse to drop the labels if reality disagrees.
    in_clause = ", ".join(f"'{v}'" for v in _DEAD_VALUES)
    found = bind.execute(
        sa.text(
            f"SELECT COUNT(*) FROM invoices WHERE workflow_state IN ({in_clause})"
        )
    ).scalar()
    if found:
        raise RuntimeError(
            f"Refusing to drop {_DEAD_VALUES} from {_ENUM_NAME}: {found} invoice(s) "
            "currently hold one of these values. C1.9 assumed they were unreachable "
            "in production — investigate before removing the enum labels."
        )

    new_enum = sa.Enum(*_LIVE_VALUES, name=f"{_ENUM_NAME}_new")
    new_enum.create(bind, checkfirst=True)
    # The column has a `DEFAULT 'draft'` (server_default) tied to the old type;
    # Postgres will not auto-cast a stored default expression across an ALTER
    # TYPE, so drop it, retype, then restore it against the new type.
    op.execute("ALTER TABLE invoices ALTER COLUMN workflow_state DROP DEFAULT")
    op.execute(
        f"ALTER TABLE invoices ALTER COLUMN workflow_state TYPE {_ENUM_NAME}_new "
        f"USING workflow_state::text::{_ENUM_NAME}_new"
    )
    op.execute(f"DROP TYPE {_ENUM_NAME}")
    op.execute(f"ALTER TYPE {_ENUM_NAME}_new RENAME TO {_ENUM_NAME}")
    op.execute("ALTER TABLE invoices ALTER COLUMN workflow_state SET DEFAULT 'draft'")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Adding a label back is natively supported without recreating the type;
    # safe because no row can hold a value between this drop and its re-add.
    for value in _DEAD_VALUES:
        op.execute(f"ALTER TYPE {_ENUM_NAME} ADD VALUE IF NOT EXISTS '{value}'")
