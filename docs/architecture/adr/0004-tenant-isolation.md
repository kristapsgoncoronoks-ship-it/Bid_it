# ADR-0004 — Shared-schema tenant isolation with an ORM guard (+ RLS)

**Status:** Accepted (all three layers implemented; RLS enforced on Postgres)

## Context
Multi-tenant financial data. A cross-tenant leak is an existential, GDPR-reportable event. We need strong isolation at low operational cost, with room to harden.

## Selected approach
**Shared schema, row-level `org_id`** on every tenant table, with **defence in depth**:
1. Explicit per-route `org_id` filters.
2. An ORM `do_orm_execute` guard that ANDs `org_id == current_org` onto every SELECT touching a **registered** tenant model (`TENANT_MODELS`), via `with_loader_criteria`. Context is set from the authenticated user's DB row, never client input.
3. Postgres **RLS** policies as a database-level backstop (Phase 2, implemented). Every tenant table has `ENABLE`/`FORCE ROW LEVEL SECURITY` + a `tenant_isolation` policy keyed on the per-transaction GUC `app.current_org`, which the app mirrors from its tenant ContextVar (`after_begin` hook + an explicit set in `get_current_user`). GUC unset ⇒ policy passes (bootstrap/operator/worker, matching the app guard); GUC set ⇒ rows restricted. `FORCE` makes it apply even to the table-owner app role. Verified against real Postgres: a raw query bypassing the ORM guard sees only the scoped tenant, and a cross-tenant insert is refused by `WITH CHECK`.

Mandatory rules (both CI-enforced): any table with `org_id` must be registered in `TENANT_MODELS`, and must appear in the RLS migration's table list — a test fails otherwise. **The app must run as a non-superuser** (superusers bypass RLS even with `FORCE`).

## Alternatives considered
- **Schema-per-tenant** — stronger isolation, but migration/ops complexity explodes with tenant count; connection/catalog bloat.
- **Database-per-tenant** — strongest isolation, highest cost; reserved as an Enterprise option.
- **App-filters only (no guard)** — one forgotten `WHERE` = a leak. Unacceptable.

## Why appropriate
Shared-schema is cheapest to run and migrate; the ORM guard means a *forgotten filter cannot leak*; RLS adds a database-enforced backstop even against raw queries/bugs above the ORM. The same API moves to schema/DB-per-tenant later with no interface change.

## Risks
- Guard depends on model registration + SQLAlchemy internals → CI test + isolation tests + version pinning.
- Raw SQL or a child-table query with user input could bypass → forbidden by rule; RLS closes the residual gap.

## Revisit when
An Enterprise customer requires physical isolation (→ DB-per-tenant option), or a scale/blast-radius analysis favours schema-per-tenant.
