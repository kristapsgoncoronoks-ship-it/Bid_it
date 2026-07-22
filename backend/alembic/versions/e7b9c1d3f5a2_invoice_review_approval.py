"""invoice review & approval workflow (Phase 08)

Adds the AP review-&-approval lifecycle on top of the intake pipeline:
- invoices: `workflow_state` (14-state lifecycle), `version` (optimistic
  concurrency), `locked_at`/`locked_by` (record lock after approval),
  `legal_entity_id` (approval-policy dimension), `account_code` (GL assignment);
- approval_policies + approval_steps (routing engine);
- invoice_comments + invoice_attachments (reviewer collaboration).

All four new tables are tenant-scoped → composite FKs + Postgres RLS.

Revision ID: e7b9c1d3f5a2
Revises: b7e1c9a4d2f0
Create Date: 2026-07-22 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.models.base


revision: str = 'e7b9c1d3f5a2'
down_revision: Union[str, None] = 'b7e1c9a4d2f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = (
    "approval_policies",
    "approval_steps",
    "invoice_comments",
    "invoice_attachments",
)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)

_TS = dict(server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False)

WF_VALUES = (
    "uploaded", "processing", "review_required", "draft", "submitted",
    "partially_approved", "approved", "rejected", "scheduled_for_payment",
    "partially_paid", "paid", "disputed", "cancelled", "archived",
)


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # --- invoices: additive lifecycle columns -------------------------------
    wf_enum = sa.Enum(*WF_VALUES, name="invoice_workflow_state")
    if is_pg:
        wf_enum.create(bind, checkfirst=True)
    op.add_column(
        "invoices",
        sa.Column(
            "workflow_state",
            sa.Enum(*WF_VALUES, name="invoice_workflow_state", create_type=False),
            nullable=False,
            server_default="draft",
        ),
    )
    op.add_column("invoices", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("invoices", sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invoices", sa.Column("locked_by", sa.String(length=200), nullable=True))
    op.add_column(
        "invoices", sa.Column("legal_entity_id", app.models.base.GUID(), nullable=True)
    )
    op.add_column("invoices", sa.Column("account_code", sa.String(length=80), nullable=True))
    op.add_column("invoices", sa.Column("submitted_by", app.models.base.GUID(), nullable=True))
    op.add_column("invoices", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_invoices_workflow_state", "invoices", ["workflow_state"])
    op.create_index("ix_invoices_legal_entity_id", "invoices", ["legal_entity_id"])
    # Backfill: an already-paid invoice is workflow-state 'paid'; everything else
    # starts at the 'draft' default (a reasonable resting point for demo/legacy rows).
    op.execute("UPDATE invoices SET workflow_state='paid' WHERE status='paid'")

    # --- approval_policies ---------------------------------------------------
    op.create_table(
        "approval_policies",
        sa.Column("id", app.models.base.GUID(), nullable=False),
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("min_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("department_id", app.models.base.GUID(), nullable=True),
        sa.Column("cost_center_id", app.models.base.GUID(), nullable=True),
        sa.Column("legal_entity_id", app.models.base.GUID(), nullable=True),
        sa.Column("vendor_id", app.models.base.GUID(), nullable=True),
        sa.Column("approver_ids", sa.Text(), nullable=True),
        sa.Column("finance_final", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("finance_approver_id", app.models.base.GUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id", name="uq_approval_policies_org_id"),
    )
    op.create_index("ix_approval_policies_org_id", "approval_policies", ["org_id"])
    op.create_index(
        "ix_approval_policies_org_active", "approval_policies", ["org_id", "active", "priority"]
    )

    # --- approval_steps ------------------------------------------------------
    op.create_table(
        "approval_steps",
        sa.Column("id", app.models.base.GUID(), nullable=False),
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("invoice_id", app.models.base.GUID(), nullable=False),
        sa.Column("policy_id", app.models.base.GUID(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="approver"),
        sa.Column("approver_id", app.models.base.GUID(), nullable=True),
        sa.Column("approver_email", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("decided_by", app.models.base.GUID(), nullable=True),
        sa.Column("decided_by_email", sa.String(length=200), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "invoice_id"], ["invoices.org_id", "invoices.id"],
            name="fk_approval_steps_invoice", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "invoice_id", "seq", name="uq_approval_steps_invoice_seq"),
    )
    op.create_index("ix_approval_steps_org_id", "approval_steps", ["org_id"])
    op.create_index("ix_approval_steps_invoice", "approval_steps", ["org_id", "invoice_id"])
    op.create_index(
        "ix_approval_steps_approver", "approval_steps", ["org_id", "approver_id", "status"]
    )

    # --- invoice_comments ----------------------------------------------------
    op.create_table(
        "invoice_comments",
        sa.Column("id", app.models.base.GUID(), nullable=False),
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("invoice_id", app.models.base.GUID(), nullable=False),
        sa.Column("author_id", app.models.base.GUID(), nullable=True),
        sa.Column("author_name", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "invoice_id"], ["invoices.org_id", "invoices.id"],
            name="fk_invoice_comments_invoice", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invoice_comments_org_id", "invoice_comments", ["org_id"])
    op.create_index("ix_invoice_comments_invoice", "invoice_comments", ["org_id", "invoice_id"])

    # --- invoice_attachments -------------------------------------------------
    op.create_table(
        "invoice_attachments",
        sa.Column("id", app.models.base.GUID(), nullable=False),
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("invoice_id", app.models.base.GUID(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime", sa.String(length=120), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("internal", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("uploaded_by", app.models.base.GUID(), nullable=True),
        sa.Column("uploaded_by_email", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id", "invoice_id"], ["invoices.org_id", "invoices.id"],
            name="fk_invoice_attachments_invoice", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "id", name="uq_invoice_attachments_org_id"),
    )
    op.create_index("ix_invoice_attachments_org_id", "invoice_attachments", ["org_id"])
    op.create_index(
        "ix_invoice_attachments_invoice", "invoice_attachments", ["org_id", "invoice_id"]
    )

    if is_pg:
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    if is_pg:
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_table("invoice_attachments")
    op.drop_table("invoice_comments")
    op.drop_table("approval_steps")
    op.drop_table("approval_policies")
    op.drop_index("ix_invoices_legal_entity_id", table_name="invoices")
    op.drop_index("ix_invoices_workflow_state", table_name="invoices")
    for col in (
        "submitted_at", "submitted_by", "account_code", "legal_entity_id",
        "locked_by", "locked_at", "version", "workflow_state",
    ):
        op.drop_column("invoices", col)
    if is_pg:
        sa.Enum(name="invoice_workflow_state").drop(bind, checkfirst=True)
