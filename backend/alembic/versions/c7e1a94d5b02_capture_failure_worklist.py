"""failed-capture worklist (H-1) — classified failure codes + acknowledgements

Revision ID: c7e1a94d5b02
Revises: b2d84f1e6c37
Create Date: 2026-08-14

A capture that failed was reachable only by polling its own id. Nothing
enumerated failures for a tenant, so a document the customer believes was
processed could silently never become an invoice.

Two additions, both purely additive:

* `extraction_runs.failure_code` / `inbound_invoices.failure_code` — the
  CLASSIFIED cause, a stable code from `services/capture_failures.py::KINDS`.
  The existing free-text `note`/`error` columns keep the raw library message for
  an engineer; the code is what the operator's worklist reasons over. Nothing is
  back-filled: a failure recorded before this column existed genuinely did not
  record a classified cause, and deriving one by matching words in an old
  message would manufacture a fact. Those rows read as `unknown_failure`, which
  claims nothing.

* `capture_acknowledgements` — append-only; one row per acknowledgement, not per
  failure, so the history survives. `failure_seen_at` pins each acknowledgement
  to the failure it was made against, so a capture that fails AGAIN returns to
  the worklist rather than inheriting the earlier dismissal.
"""

from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa

from alembic import op

revision: str = "c7e1a94d5b02"
down_revision: Union[str, None] = "b2d84f1e6c37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("capture_acknowledgements",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    op.add_column("extraction_runs", sa.Column("failure_code", sa.String(length=40), nullable=True))
    op.add_column(
        "inbound_invoices", sa.Column("failure_code", sa.String(length=40), nullable=True)
    )

    op.create_table(
        "capture_acknowledgements",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("channel", sa.String(length=12), nullable=False),
        sa.Column("ref_id", app.models.base.GUID(), nullable=False),
        sa.Column("acknowledged_by", sa.String(length=320), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("failure_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", app.models.base.GUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_capture_acknowledgements_org_id"),
        "capture_acknowledgements",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_capture_acks_org_ref",
        "capture_acknowledgements",
        ["org_id", "channel", "ref_id"],
        unique=False,
    )

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
    op.drop_index("ix_capture_acks_org_ref", table_name="capture_acknowledgements")
    op.drop_index(
        op.f("ix_capture_acknowledgements_org_id"), table_name="capture_acknowledgements"
    )
    op.drop_table("capture_acknowledgements")
    op.drop_column("inbound_invoices", "failure_code")
    op.drop_column("extraction_runs", "failure_code")
