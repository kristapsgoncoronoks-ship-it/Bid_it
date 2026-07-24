"""Postgres row-level security backstop for tenant isolation (ADR-0004)

Enables RLS on every tenant-scoped table so the database itself refuses
cross-tenant rows, even for a raw query or a bug above the ORM tenant guard.
FORCE ensures the policy applies even to the table owner (the app role in simple
deployments). The policy keys off the per-transaction GUC `app.current_org`,
which the app keeps in sync with its tenant context (see app/core/tenant.py):

  * GUC unset  → policy passes all rows (bootstrap / platform-operator / worker
                 -claim paths run intentionally unscoped, matching the app guard).
  * GUC set    → rows restricted to that org (the normal authenticated path).

Postgres-only: on SQLite (dev/CI) this migration is a no-op, so the portable test
suite is unaffected. A superuser bypasses RLS — the app must NOT run as one.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-20 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every tenant-scoped table (mirrors app.core.tenant.TENANT_MODELS). Child tables
# with no org_id (line_items, issued_invoice_lines, expense_items) are reached
# only via an already-scoped parent and carry no policy.
TENANT_TABLES = (
    "audit_events", "budget_targets", "email_intakes", "email_messages",
    "expense_comments", "expense_reports", "expense_transactions", "inbound_invoices",
    "invitations", "invoices", "issued_invoices", "issuer_profiles", "jobs",
    "org_modules", "partner_documents", "partners", "recurring_invoices",
    "usage_counters", "users", "vendors", "webhook_deliveries", "webhook_endpoints",
)

_PREDICATE = (
    "current_setting('app.current_org', true) IS NULL "
    "OR org_id::text = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # RLS is Postgres-only; no-op on SQLite (dev/CI)
    for t in TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for t in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
