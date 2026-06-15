# Multi-tenancy program plan

This is the program plan for turning the Fleet Fuel & VAT Refund System from a
per-deployment, single-tenant product into a multi-tenant SaaS where several
unrelated client companies share one installation behind **provable tenant
isolation**.

A cross-tenant data leak is a GDPR Art. 33/34 reportable breach. This program is
therefore engineered to **de-risk before it retrofits**: P0 (this slice) ships
the foundation mechanism with **zero behavior change**, and every later phase
moves one table at a time, each gated on an automated cross-tenant test. Nothing
filters or alters an existing query until its table has been migrated AND tested.

> Read this together with `docs/EVOLUTION_PLAN.md` §4.1 (the RLS decision + the
> two footguns) and `docs/STRATEGY.md` (when multi-client is worth doing). This
> doc does not contradict that analysis; it operationalizes it.

---

## CARDINAL INVARIANT — OFF by default = byte-identical existing behavior

The whole program is armed by a single app setting, `multitenant`, stored in
`app_settings` (security.db) like every other module switch. It defaults to
`"0"` (OFF). While OFF:

- the request hook never binds a tenant context,
- `tenancy.scope_clause()` returns `("", [])` — a literal no-op spliced into no
  SQL, so existing queries are unchanged,
- `tenancy.require_tenant()` does not raise,
- the EnvKEK/key paths and connection roles are untouched.

So a default install behaves EXACTLY as a single-tenant deployment does today.
The full existing test suite must pass unchanged; the OFF-by-default inertness
tests in `tests/test_tenancy.py` are the standing proof of that property.

**Turning the switch ON is gated** on P1+P2 being complete for every
tenant-scoped table (see Activation/rollback below). Flipping it on before then
is a configuration error, not a supported state.

---

## Recommended isolation model

**Application-level `tenant_id` scoping is the foundation**, with PostgreSQL
Row-Level Security (RLS) layered on top as defense-in-depth once we are on
Postgres.

Why this model:

- **Model-agnostic + works now.** SQLite has no RLS. App-level `tenant_id`
  scoping is the only isolation layer available on the default SQLite engine, and
  it is exactly the same mechanism we want on Postgres. Building it first means we
  are not blocked on the Postgres cutover (`docs/SCALING.md`), and the same code
  path composes cleanly with RLS later.
- **Defense in depth.** On Postgres we keep the app-level scoping AND add RLS, so
  a single forgotten `WHERE tenant_id = ?` (the OWASP API-#1 IDOR/BOLA class)
  cannot leak — the database refuses the row even if the application query is
  wrong. App scoping is still required (RLS does not give you per-object authZ
  *within* a tenant).

Why NOT the alternatives as the primary:

- **Schema-per-tenant / DB-per-tenant.** Maximum isolation, but operationally
  expensive at scale: every migration must fan out across N schemas/DBs, backups
  and connection pools multiply, and cross-tenant platform queries (billing,
  ops dashboards) get awkward. We do NOT use it as the default.
- **When DB-per-tenant IS warranted:** a single high-value or regulated tenant
  that contractually demands hard physical isolation. The architecture supports
  this as a per-tenant exception (route that tenant's `db.connect()` to its own
  database) while the long tail stays on shared-DB + RLS. This hybrid (RLS for the
  many, dedicated DB for the few) is the common end state.

---

## The RLS footguns (Postgres phase — reused from EVOLUTION_PLAN §4.1)

When we enable RLS on Postgres, two footguns silently re-open the leak if missed:

1. **RLS is bypassed by the table OWNER, superusers, and `BYPASSRLS` roles.**
   The app must connect as a **non-owner, non-superuser** role, AND every
   tenant-scoped table must be set `ALTER TABLE … FORCE ROW LEVEL SECURITY` (so
   the policy applies even to the owner). Owning-the-table is the default for the
   role that ran the migrations; the app role must be distinct.

2. **Session `SET` leaks across requests under a transaction-pooling pooler**
   (PgBouncer transaction mode). Set the tenant **per transaction** with
   `set_config('app.tenant_id', <id>, true)` (the `true` = transaction-local),
   never a session `SET`. The RLS policy reads `current_setting('app.tenant_id')`.

SQLite has no RLS, so on SQLite the **app-level `scope_clause`/`require_tenant`
scoping is the only isolation layer** — which is precisely why P2 makes the app
scoping mandatory and test-gated on every table, independent of the engine.

---

## Phased rollout

Each phase is independently shippable. The `multitenant` switch stays OFF until
the gate in Activation/rollback is met.

- **P0 — Foundation (THIS SLICE).** `tenancy.py`: tenant registry (in security.db,
  app-owned platform metadata), request-scoped thread-local tenant context
  (mirrors `audit.py`'s actor), the `multitenant` master switch (OFF by default),
  and the inert enforcement primitives `require_tenant()` / `scope_clause()`. The
  request hook in `app.py` binds/resets the context ONLY when the switch is ON.
  A read-only `/admin/tenants` surface. **No existing product query is touched.**

- **P1 — Add `tenant_id` + backfill.** Add a `tenant_id` column to each
  tenant-scoped product table via `db_migrate.apply(...)` (append at the END of
  each module's list). Backfill the existing single tenant's rows to a bootstrap
  `tenant_id` (e.g. `"default"`) so the current installation becomes "tenant
  default" with no data movement. Index `(tenant_id, …)` on hot paths. Still no
  query scoping — the column exists but is not yet read.

- **P2 — Wire scoping into every tenant-scoped query, table-by-table.** For each
  table, splice `scope_clause()` into its SELECT/UPDATE/DELETE (and stamp
  `current_tenant()` / `require_tenant()` on INSERT), and add a **cross-tenant
  access test** that seeds two tenants and asserts no bleed (see strategy below).
  On Postgres, also create the RLS policy + `FORCE ROW LEVEL SECURITY` for that
  table. One table = one reviewable change = one test. A table is "done" only when
  its cross-tenant test is green.

- **P3 — Connection + key enforcement.** Run app traffic as the non-owner DB role
  and enforce `set_config('app.tenant_id', …, true)` per transaction in
  `db.connect()`. Wire per-tenant EnvKEK/BYOK keys (`keyvault.py` already takes a
  `tenant` arg) so the platform cannot bulk-decrypt one tenant's stored secrets.

- **P4 — Onboarding + tenant resolution.** Tenant onboarding/admin UI (create,
  activate, deactivate) on `/admin/tenants` (P0 ships it read-only). Resolve the
  request's tenant from subdomain and/or session — the `_resolve_tenant()` seam in
  `app.py` is the single place this lands; today it returns the session tenant or
  the `"default"` bootstrap and is only called when the switch is ON.

- **P5 — Posture.** SOC 2 Type II + ISO 27001/27017/27018, pen-test proving no
  cross-tenant leak, audit/DPA/breach-runbook for Art. 33/34, per-tenant data
  export/erasure (GDPR Art. 15/17). Gate go-multi-client on this.

---

## Cross-tenant test strategy

The non-negotiable acceptance bar for P2 (and the gate to flip the switch):

- **Every tenant-scoped read must be proven to return ONLY the current tenant's
  rows.** The harness seeds (at least) two tenants — A and B — with overlapping
  data, binds the context to A, runs the real query path, and asserts the result
  contains A's rows and **none** of B's. Then it flips to B and asserts the
  mirror. A bleed in either direction fails the test.
- `tests/test_tenancy.py::test_cross_tenant_scope_demo` is the **template** for
  this shape (it demonstrates the assertion against a throwaway table without
  touching any product query). Each P2 table change copies this shape against the
  real query.
- On Postgres, add a second harness layer that runs as the **non-owner app role**
  and asserts RLS blocks a deliberately unscoped query — proving the DB layer
  catches a forgotten `WHERE` (defense in depth).
- A cross-tenant leak found in CI is treated as a release blocker; a leak found in
  production is a GDPR Art. 33/34 reportable breach.

---

## Activation / rollback

- **Default:** `multitenant = "0"` (OFF). Single-tenant, inert, byte-identical to
  today. This is the only supported state until the gate below is met.
- **Gate to turn ON:** P1 (column + backfill) AND P2 (query scoping + a green
  cross-tenant test) complete for **every** tenant-scoped table touched by any
  reachable route, plus P3 connection/key enforcement on Postgres. Turning the
  switch on before then is unsupported and unsafe.
- **Rollback:** flip `multitenant` back to `"0"`. Because P1 only ADDS a column
  (the bootstrap tenant owns all existing rows) and P2's `scope_clause` is a
  no-op while OFF, turning the switch off restores single-tenant behavior without
  data migration. (Per-tenant encrypted secrets from P3 remain per-tenant; that is
  forward-compatible and needs no rollback.)
