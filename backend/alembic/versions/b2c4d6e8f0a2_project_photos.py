"""WO-F job photos: project_documents kind CHECK widens to admit 'photo'.

No new tables, no new columns — the photos ride the existing
content-addressed document path; only the closed kind set grows. Postgres
alters the constraint in place; SQLite batch-rebuilds (same pattern as
f0a2b4c6d8e0, which widened the same CHECK for 'acceptance').

Revision ID: b2c4d6e8f0a2
Revises: a1b3c5d7e9f1
Create Date: 2026-08-24
"""

from __future__ import annotations

from alembic import op

revision = "b2c4d6e8f0a2"
down_revision = "a1b3c5d7e9f1"
branch_labels = None
depends_on = None

_OLD_KIND = "kind IN ('contract', 'acceptance', 'other')"
_NEW_KIND = "kind IN ('contract', 'acceptance', 'photo', 'other')"


def _swap_check(constraint_sql: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE project_documents DROP CONSTRAINT ck_project_documents_kind")
        op.execute(
            "ALTER TABLE project_documents "
            f"ADD CONSTRAINT ck_project_documents_kind CHECK ({constraint_sql})"
        )
    else:
        with op.batch_alter_table("project_documents") as batch:
            batch.drop_constraint("ck_project_documents_kind", type_="check")
            batch.create_check_constraint("ck_project_documents_kind", constraint_sql)


def upgrade() -> None:
    _swap_check(_NEW_KIND)


def downgrade() -> None:
    _swap_check(_OLD_KIND)
