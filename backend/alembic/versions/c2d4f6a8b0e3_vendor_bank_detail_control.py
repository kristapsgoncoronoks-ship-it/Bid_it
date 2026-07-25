"""vendor bank-detail control (WO-2)

Adds the payment-redirection fraud controls to the vendor master:

- `vendors.version` (optimistic concurrency) and `vendors.status`
  (`active` | `provisional`) — existing rows backfill to version=1 / 'active'
  via the server defaults; NO existing data is modified or cleared.
- `vendor_change_requests` — the second-approver workflow for the protected
  fields (`iban`, `tax_id`): a change on an existing vendor lands here as
  `pending` and only a different SETTINGS_MANAGE holder may apply it.
  Tenant-scoped, composite FK (org_id, vendor_id) → vendors(org_id, id),
  RLS policy in THIS migration (invariant §4.2).
- A partial unique index allowing at most ONE open request per (vendor, field).
- An advisory REPORT of existing vendors whose stored IBAN is structurally
  invalid (count + vendor ids, never the IBANs themselves). The data is left
  ALONE — deleting or clearing a customer's data in a migration is never
  acceptable; the SEPA builder independently refuses to pay an invalid IBAN.

DOWNGRADE POLICY: the downgrade REFUSES to run while any change request is
still `pending`. Silently dropping a pending bank-detail change would discard
an in-flight approval decision (and silently applying it would bypass the
second approver). An operator must approve or reject the pending rows first —
an explicit human decision, on purpose.

Revision ID: c2d4f6a8b0e3
Revises: d9a1c3e5b7f2
Create Date: 2026-07-25 08:00:00.000000
"""

import logging
from typing import Sequence, Union

import app.models.base  # portable GUID type used by every table
import sqlalchemy as sa
from alembic import op

from app.core import bank_id

revision: str = "c2d4f6a8b0e3"
down_revision: Union[str, None] = "d9a1c3e5b7f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

TENANT_TABLES = ("vendor_change_requests",)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def _report_invalid_ibans() -> None:
    """Advisory report only — flags rows a structural IBAN check rejects, so an
    operator can chase them. Never mutates; never logs a full IBAN."""
    rows = op.get_bind().execute(
        sa.text("SELECT id FROM vendors WHERE iban IS NOT NULL AND iban != ''")
    )
    ids = [r[0] for r in rows]
    invalid: list[str] = []
    if ids:
        for row in op.get_bind().execute(
            sa.text("SELECT id, iban FROM vendors WHERE iban IS NOT NULL AND iban != ''")
        ):
            if not bank_id.is_valid_iban(row[1]):
                invalid.append(str(row[0]))
    log.warning(
        "vendor IBAN report: %d vendor(s) with an IBAN, %d structurally invalid%s "
        "(data left untouched; the SEPA builder refuses to pay an invalid IBAN)",
        len(ids),
        len(invalid),
        f" — vendor ids: {', '.join(invalid)}" if invalid else "",
    )


def upgrade() -> None:
    with op.batch_alter_table("vendors", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active")
        )
        batch_op.create_unique_constraint("uq_vendors_org_id", ["org_id", "id"])

    op.create_table(
        "vendor_change_requests",
        sa.Column("org_id", app.models.base.GUID(), nullable=False),
        sa.Column("vendor_id", app.models.base.GUID(), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("old_value", sa.String(length=128), nullable=True),
        sa.Column("new_value", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("requested_by", app.models.base.GUID(), nullable=False),
        sa.Column("requested_by_email", sa.String(length=200), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("decided_by", app.models.base.GUID(), nullable=True),
        sa.Column("decided_by_email", sa.String(length=200), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("source_document_id", app.models.base.GUID(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["org_id", "vendor_id"],
            ["vendors.org_id", "vendors.id"],
            name="fk_vendor_change_requests_vendor",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vendor_change_requests_org_id"),
        "vendor_change_requests",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_vendor_change_requests_org_vendor_status",
        "vendor_change_requests",
        ["org_id", "vendor_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_vendor_change_requests_open_field",
        "vendor_change_requests",
        ["org_id", "vendor_id", "field"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )

    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {t} "
                f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
            )

    _report_invalid_ibans()


def downgrade() -> None:
    # See the module docstring: pending change requests force an explicit
    # operator decision — the downgrade neither applies nor drops them.
    pending = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM vendor_change_requests WHERE status = 'pending'")
    ).scalar()
    if pending:
        raise RuntimeError(
            f"Refusing to downgrade: {pending} vendor change request(s) are still "
            "'pending'. Approve or reject them first — dropping them would discard "
            "an in-flight bank-detail approval decision."
        )
    if op.get_bind().dialect.name == "postgresql":
        for t in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
    op.drop_index("uq_vendor_change_requests_open_field", table_name="vendor_change_requests")
    op.drop_index(
        "ix_vendor_change_requests_org_vendor_status", table_name="vendor_change_requests"
    )
    op.drop_index(
        op.f("ix_vendor_change_requests_org_id"), table_name="vendor_change_requests"
    )
    op.drop_table("vendor_change_requests")
    with op.batch_alter_table("vendors", schema=None) as batch_op:
        batch_op.drop_constraint("uq_vendors_org_id", type_="unique")
        batch_op.drop_column("status")
        batch_op.drop_column("version")
