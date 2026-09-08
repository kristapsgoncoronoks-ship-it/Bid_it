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

## Addendum 2026-09-08 — the guard's mechanism (PERF-019, reference integration R5)

Layer 2 keeps the same set of scoped models and the same predicates, and changes HOW it attaches them (one aliasing difference was found and fixed by the R5 panel before certification — below). Until R5 the
`do_orm_execute` hook attached one `with_loader_criteria` option per
registered model — 103 of them, plus 5 soft-delete criteria — to every SELECT
the process issued, and SQLAlchemy regenerated the statement's cache key over
all of them on every execute. Measured on the perf workspace
(`docs/perf/TENANT-GUARD-2026-09-08.md`): roughly half of every request's loop
time; with the guard attaching nothing (a measurement only, RLS still on) every
endpoint's serial p95 fell 40–50 % and throughput rose 1.7–2.3×.

Now the hook attaches ONE option on the declarative `Base` (and one for the
soft-delete set). Its callable decides per mapped class, by attribute through
the mapper: a class whose mapper has an `org_id` column is scoped by its org
column, `User` by membership existence (as before), a class without `org_id`
gets `true()` — which is why every other entity's SQL now ends in `AND true`
(harmless, visible in captured statements). The callable receives the ALIAS
for an aliased entity; the first draft checked `model is User` and so gave
`aliased(User)` the active-org pointer predicate — caught by the panel, fixed
by resolving the class through `inspect(cls).mapper` and building the
predicate on the alias's own columns, with an aliased-`User` test. The tenant id enters the
callable as a typed bind parameter created outside it — SQLAlchemy's lambda
analysis then makes the option's cache key structural and carries the value
with each execution, so two tenants share one compiled statement and never each
other's rows.

What the change keeps, and what proves it (`tests/test_tenant_guard_mechanism.py`
plus the existing suites):

- the scoped SET is exactly the registry — `test_tenant_registration.py` fails
  when a model grows `org_id` without registering, `test_rls.py` when the RLS
  table list drifts from the registry; the coverage test asserts the option
  scopes precisely `TENANT_MODELS` and nothing else;
- an empty registry attaches nothing, so `test_tenancy_parity.py`'s self-test
  (layer 2 neutralised, the probe must fail) still can fail;
- the reach is identical to the old mechanism on eighteen statement shapes
  probed on Postgres by the panel: entities in the columns clause,
  `select_from`, explicit joins, eager-load joins (criteria in the ON clause),
  aliases (`include_aliases=True`), relationship loads issued as statements
  (selectin / subquery / lazy — scoped by the PARENT load's org; a cross-org
  `vendor_id`, structurally possible because that FK is plain — DB-020 — loads
  as `None`), ORM-enabled nested selects and unions;
- what neither mechanism reaches, stated precisely: a table that enters a
  statement only through its WHERE clause (top level or nested — the
  `approval_policy` EXISTS, a Core `exists()`), identity-map hits
  (`Session.get`, an already-loaded many-to-one), `refresh()` / expired
  attribute loads, Core table selects, `text()`, and writes — a service scopes
  those itself, RLS behind it;
- alternating tenants on the same statement in one process return each
  tenant's rows; the two options' statement cache keys are equal while their
  extracted bind values differ, and the compiled cache grows by one entry,
  never per tenant (a third tenant after warm-up hits the same entry). The
  row assertions are the proof — a flat cache alone would not detect a
  baked-in tenant.

Two ways this could have gone wrong, both hit and both caught while building
it: hiding the tenant parameter from the lambda analysis (a default argument)
baked the first tenant's value into the compiled statement and served its rows
to the second — the two-tenant test failed on the first run; and reading the
registry tuple inside the callable made SQLAlchemy treat the tuple as a bound
value. Both are recorded in `_tenant_options`'s docstring so they are not tried
again.

Rollback: the mechanism is four files with no migration and no configuration;
revert the commit and restart (the per-org option cache is in-process). While
a revert is pending, RLS (layer 3) bounds any leak on Postgres. There is no
runtime switch by design — a switch would be a second mechanism to prove.

Revisit if SQLAlchemy changes how `with_loader_criteria` resolves a callable on
a base class (the pin is exact, `sqlalchemy[asyncio]==2.0.51`; the mechanism
tests reach the private API on purpose so a bump breaks loudly), or if a model
ever needs a tenant predicate that is neither its `org_id` nor membership.
