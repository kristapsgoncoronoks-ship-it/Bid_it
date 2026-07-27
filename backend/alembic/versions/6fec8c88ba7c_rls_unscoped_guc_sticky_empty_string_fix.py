"""RLS: fix the "unscoped" GUC losing its NULL identity on a reused connection (WO-27)

Every RLS policy so far (b2c3d4e5f6a7 and every migration that added a tenant
table since) reads `current_setting('app.current_org', true) IS NULL` as "the
app is running unscoped (bootstrap / platform-operator / worker-claim) —
bypass". That is only true on a virgin Postgres backend connection.

**The gotcha (confirmed empirically on Postgres 16 via psql, reproduced
2026-07-27 — see ADR-0028):**

    BEGIN;
    SELECT set_config('app.current_org', 'orgA', true);
    SELECT current_setting('app.current_org', true);  -- 'orgA'
    COMMIT;

    BEGIN;
    SELECT current_setting('app.current_org', true);  -- '' , NOT NULL!
    COMMIT;

Once `set_config('app.current_org', :org, true)` (a `SET LOCAL`) has run on a
given physical connection — even once, even for a legitimately-scoped
request — `current_setting(name, true)` never returns SQL NULL again for the
rest of that connection's life. Neither the transaction ending (COMMIT), nor
`RESET app.current_org`, nor an explicit `set_config(name, NULL, true)`
restores the NULL state; all three still yield `''`. This is a real Postgres
custom-GUC quirk (`SET LOCAL`'s "reset" value is the value the GUC had at
transaction start OUTSIDE of any transaction that ever set it, and a
never-declared custom GUC's true out-of-transaction default is `''`, not
NULL, from the moment it is first touched).

Consequence pre-fix: on ANY connection a prior request scoped (i.e. under any
real pool — pgbouncer or SQLAlchemy's own pool — under any load at all),
every subsequent UNSCOPED query silently sees ZERO rows instead of "all
rows" — hiding, not leaking, but still a correctness/availability defect.
Concretely `get_current_identity`'s first, deliberately-unscoped query
(`db.get(User, ...)` in `app/api/deps.py::_authenticate`, before the org is
known) returns `None` on a reused connection, so a perfectly valid token
intermittently 401s depending only on which physical connection the pool
hands back — see `tests/test_rls_connection_reuse.py`, which reproduces this
red-then-green.

**The fix (Option B from WO-27 — policy-side, chosen over an app-side
sentinel):** every policy's "unscoped" check now ALSO treats the sticky `''`
as unscoped:

    current_setting('app.current_org', true) IS NULL
    OR current_setting('app.current_org', true) = ''
    OR org_id::text = current_setting('app.current_org', true)

Chosen over the sentinel-string alternative (always `set_config` a reserved
value like `'__unscoped__'` on `after_begin`, policies check `= sentinel`
instead of `IS NULL`) because it is a single, uniform SQL-pattern change with
no new invariant to keep ("never let a real org id equal the sentinel", "never
skip `after_begin`'s guard") and no plumbing change in `app/core/tenant.py` —
lower risk for a migration that must touch every one of the 58 RLS-protected
tables in one shot. The tradeoff (noted in WO-27) is that it doesn't make the
Postgres quirk itself go away, just engineers around both values it can
produce; this docstring is the record so a future reader doesn't have to
rediscover the reproduction.

`users` gets its own predicate here (mirrors e6a8c0b2d4f6's membership-driven
policy, just with the same `= ''` addition to its "unscoped" leg) rather than
the generic `org_id::text = ...` predicate every other table uses.

Postgres-only; a no-op on SQLite (dev/CI), where `_sync_rls_org`/
`apply_db_tenant` are already no-ops per their own dialect check and the ORM
guard (`app.core.tenant._apply_tenant_scope`) is the only enforcement layer —
so this migration changes no SQLite-observable behaviour.

No behaviour change to the SCOPED path (the overwhelming majority of
requests): `org_id::text = current_setting(...)` is untouched; only the
"what counts as unscoped" leg gained a second value.

Revision ID: 6fec8c88ba7c
Revises: 1507ce3eb95f
Create Date: 2026-07-27 21:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = '6fec8c88ba7c'
down_revision: Union[str, None] = '1507ce3eb95f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The full RLS-protected table set as of this migration (mirrors
# app.core.tenant.TENANT_MODELS minus `users`, which keeps its own
# membership-driven predicate below). Declared here — even though every table
# already appears in an earlier migration's TENANT_TABLES — because this
# migration is the one that actually (re)defines each policy; the set-equality
# guard (tests/test_rls.py::_rls_migration_tables) unions across migrations,
# so listing the full set here is accurate and self-documenting, not double
# counting (it's a set union).
TENANT_TABLES = (
    "approval_policies", "approval_steps", "audit_events", "auth_tokens",
    "bank_lines", "bank_statements", "billing_payments", "budget_targets",
    "cost_centers", "currencies", "customer_contacts", "customers",
    "departments", "document_versions", "documents", "dunning_policies",
    "email_intakes", "email_messages", "expense_approval_policies",
    "expense_approval_steps", "expense_comments", "expense_items",
    "expense_policies", "expense_reports", "expense_transactions",
    "extraction_fields", "extraction_runs", "inbound_invoices", "invitations",
    "invoice_attachments", "invoice_comments", "invoices",
    "issued_invoice_attachments", "issued_invoices", "issuer_profiles", "jobs",
    "legal_holds", "memberships", "org_modules", "partner_documents",
    "partners", "payment_runs", "payments", "projects", "receipts",
    "recurring_invoices", "reimbursement_batches", "retention_policies",
    "sessions", "sso_connections", "supplier_payments", "tax_codes",
    "usage_counters", "users", "vendor_change_requests", "vendors",
    "webhook_deliveries", "webhook_endpoints",
)

# Tables the generic org_id-equality predicate applies to (everything except
# `users`, which is membership-driven — see e6a8c0b2d4f6).
_GENERIC_TABLES = tuple(t for t in TENANT_TABLES if t != "users")

_UNSET = (
    "current_setting('app.current_org', true) IS NULL "
    "OR current_setting('app.current_org', true) = ''"
)
_ORG_MATCH = "org_id::text = current_setting('app.current_org', true)"
_PREDICATE = f"{_UNSET} OR {_ORG_MATCH}"

# Pre-fix predicates, restored verbatim on downgrade.
_LEGACY_UNSET = "current_setting('app.current_org', true) IS NULL"
_LEGACY_PREDICATE = f"{_LEGACY_UNSET} OR {_ORG_MATCH}"

# users: membership-driven (e6a8c0b2d4f6), with the same `= ''` addition.
_MEMBER_EXISTS = (
    "EXISTS (SELECT 1 FROM memberships m "
    "WHERE m.user_id = users.id "
    "AND m.org_id::text = current_setting('app.current_org', true))"
)
_USERS_USING = f"{_UNSET} OR {_MEMBER_EXISTS}"
_USERS_CHECK = f"{_UNSET} OR {_ORG_MATCH} OR {_MEMBER_EXISTS}"

_LEGACY_USERS_USING = f"{_LEGACY_UNSET} OR {_MEMBER_EXISTS}"
_LEGACY_USERS_CHECK = f"{_LEGACY_UNSET} OR {_ORG_MATCH} OR {_MEMBER_EXISTS}"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # RLS is Postgres-only; no-op on SQLite (dev/CI)
    for t in _GENERIC_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
        )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON users")
    op.execute(
        "CREATE POLICY tenant_isolation ON users "
        f"USING ({_USERS_USING}) WITH CHECK ({_USERS_CHECK})"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for t in _GENERIC_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING ({_LEGACY_PREDICATE}) WITH CHECK ({_LEGACY_PREDICATE})"
        )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON users")
    op.execute(
        "CREATE POLICY tenant_isolation ON users "
        f"USING ({_LEGACY_USERS_USING}) WITH CHECK ({_LEGACY_USERS_CHECK})"
    )
