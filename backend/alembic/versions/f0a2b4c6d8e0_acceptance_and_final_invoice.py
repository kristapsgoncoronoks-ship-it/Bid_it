"""acceptance & handover + the final-invoice gate toggle

Revision ID: f0a2b4c6d8e0
Revises: e9f1a3b5c7d9
Create Date: 2026-08-23

WO-D (docs/design/project-profitability.md §5a, the phase-5 remainder).
Additive columns only — no new tables, no new RLS surface:

- projects: acceptance recorded as a stamped EVENT (accepted_at/by, the
  linked acceptance document, a note) — not a new status.
- organizations: the per-org final-invoice gate toggle (owner decision:
  linked by default, gated on opt-in).
- project_documents: the `kind` CHECK widens to admit 'acceptance' —
  batch-alter so the SQLite clean-from-empty migration test can rebuild
  the table; Postgres alters the constraint in place.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID

revision: str = "f0a2b4c6d8e0"
down_revision: Union[str, None] = "e9f1a3b5c7d9"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

_OLD_KIND = "kind IN ('contract', 'other')"
_NEW_KIND = "kind IN ('contract', 'acceptance', 'other')"


def upgrade() -> None:
    op.add_column(
        "projects", sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("projects", sa.Column("accepted_by", sa.String(255), nullable=True))
    op.add_column("projects", sa.Column("acceptance_document_id", GUID(), nullable=True))
    op.add_column("projects", sa.Column("acceptance_note", sa.Text(), nullable=True))

    op.add_column(
        "organizations",
        sa.Column(
            "final_invoice_requires_acceptance",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE project_documents DROP CONSTRAINT ck_project_documents_kind")
        op.execute(
            f"ALTER TABLE project_documents ADD CONSTRAINT ck_project_documents_kind CHECK ({_NEW_KIND})"
        )
    else:
        with op.batch_alter_table("project_documents") as batch:
            batch.drop_constraint("ck_project_documents_kind", type_="check")
            batch.create_check_constraint("ck_project_documents_kind", _NEW_KIND)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE project_documents DROP CONSTRAINT ck_project_documents_kind")
        op.execute(
            f"ALTER TABLE project_documents ADD CONSTRAINT ck_project_documents_kind CHECK ({_OLD_KIND})"
        )
    else:
        with op.batch_alter_table("project_documents") as batch:
            batch.drop_constraint("ck_project_documents_kind", type_="check")
            batch.create_check_constraint("ck_project_documents_kind", _OLD_KIND)
    op.drop_column("organizations", "final_invoice_requires_acceptance")
    op.drop_column("projects", "acceptance_note")
    op.drop_column("projects", "acceptance_document_id")
    op.drop_column("projects", "accepted_by")
    op.drop_column("projects", "accepted_at")
