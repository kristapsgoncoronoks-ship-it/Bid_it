# ADR-0028 — RLS "unscoped" GUC: a custom `set_config` never restores SQL NULL (WO-27)

**Status:** Accepted — implemented (WO-27, board — discovered while proving WO-26/R2). Amends ADR-0004.

## Context
ADR-0004's RLS backstop (`app/core/tenant.py`, migration `b2c3d4e5f6a7`) gives every
tenant-scoped table a policy of the shape:

```sql
USING (current_setting('app.current_org', true) IS NULL OR org_id::text = current_setting(...))
```

`current_setting(name, true) IS NULL` was written to mean "the app is running
unscoped (bootstrap / platform-operator / worker-claim path) — bypass". That
reading is only correct on a **virgin** Postgres backend connection that has
never had the custom GUC touched.

**The gotcha, confirmed empirically on Postgres 16 via `psql` (scratch cluster,
NOSUPERUSER role, 2026-07-27):**

```sql
BEGIN;
SELECT set_config('app.current_org', 'orgA', true);
SELECT current_setting('app.current_org', true);  -- 'orgA'
COMMIT;

BEGIN;
SELECT current_setting('app.current_org', true);  -- '' , NOT NULL
COMMIT;

BEGIN;
SELECT set_config('app.current_org', NULL, true);  -- explicit NULL doesn't help
SELECT current_setting('app.current_org', true);   -- still ''
COMMIT;

BEGIN;
RESET app.current_org;                              -- RESET doesn't help either
SELECT current_setting('app.current_org', true);    -- still ''
COMMIT;
```

Once `set_config('app.current_org', :org, true)` (a `SET LOCAL`, scoped to the
current transaction, used deliberately so it auto-resets under pooling) has run
on a given physical connection — even once, even for a legitimately-scoped
request — `current_setting(name, true)` **never returns SQL NULL again for the
rest of that connection's life**. Neither the transaction ending (COMMIT), nor
`RESET`, nor an explicit `set_config(name, NULL, true)` restores the NULL
state; a custom (undeclared) GUC's true "untouched" value is NULL, but once any
transaction has ever `SET LOCAL`'d it, Postgres's post-transaction reset value
for that GUC becomes `''`, not NULL — and stays that way.

**Consequence:** on a connection that has EVER served a scoped (authenticated)
request, every subsequent UNSCOPED query against an RLS-protected table
returned ZERO rows — not a cross-tenant leak (fails toward hiding, not
exposing), but a silent, hard-to-debug correctness/availability defect.
Concretely, `get_current_identity` (`app/api/deps.py`) does its FIRST query —
`db.get(User, payload["sub"])` inside `_authenticate()` — deliberately
UNSCOPED (the org isn't known yet), then calls `set_current_org(user.org_id)`
afterwards. On a REUSED, already-scoped connection, that first query returns
`None` → the request gets a bare 401 "Could not validate credentials",
indistinguishable from a bad token. **On any connection pool that has been
warmed up — i.e. any real deployment under any load, pgbouncer or
SQLAlchemy's own pool — a fraction of otherwise-valid authenticated requests
intermittently 401 depending only on which physical connection the pool
happens to hand back**, and any genuinely-unscoped path (bootstrap,
platform-operator dashboards, worker-claim queries) sharing that pool risks
seeing empty results the same way.

This was never caught because no existing test combined (a) real Postgres,
(b) the full HTTP dependency-injection auth chain (`get_current_identity`'s
two-phase unscoped-then-scoped pattern), and (c) more than one authenticated
request sharing an engine/pool. `tests/test_rls.py` /
`tests/test_numbering_concurrency.py` / `tests/test_payment_run_pay_concurrency.py`
all call services directly with an explicit Python `set_current_org()`, never
exercising the unscoped-first-query window through HTTP;
`tests/test_tenancy_parity.py`'s HTTP-level probes run on the default SQLite
harness (no RLS). `tests/test_credit_note_lock_concurrency.py` (WO-26/R2) was
the first test to combine all three, and used `NullPool` (a brand-new physical
connection per session, never reused) specifically to avoid tripping this
defect while proving the credit-note lock fix — it neither fixed nor even
exercised a fix for this bug; WO-27 is that follow-up.

## Decision
**Option B — policy-side fix.** Every RLS policy's "unscoped" check now also
treats the sticky empty string as unscoped:

```sql
current_setting('app.current_org', true) IS NULL
  OR current_setting('app.current_org', true) = ''
  OR org_id::text = current_setting('app.current_org', true)
```

Shipped as migration `6fec8c88ba7c`, which `DROP POLICY` + `CREATE POLICY`s
every one of the 58 RLS-protected tables (57 with the generic org_id-equality
predicate, plus `users`'s membership-driven predicate from `e6a8c0b2d4f6` with
the same `= ''` addition) — it does not edit any shipped migration file
in place (those stay historical record; `alembic/versions/*.py`'s own
`TENANT_TABLES` lists are untouched), matching the precedent `e6a8c0b2d4f6`
already set for a later migration replacing an earlier policy.

**Rejected alternative — Option A, an app-side sentinel.** Always call
`set_config('app.current_org', :value, true)` on `after_begin` (never skip
when org is `None`), using a reserved sentinel string (e.g. `'__unscoped__'`,
guaranteed never to collide with a UUID org id) for the unscoped case, and
change every policy to check `= '__unscoped__'` instead of `IS NULL`. Rejected
because it trades one uniform SQL-pattern change for two new invariants to
hold forever ("a real org id can never equal the sentinel string" and "every
code path that might begin a transaction must remember to `set_config` the
sentinel, not skip the call") plus a plumbing change in `app/core/tenant.py`
(`_sync_rls_org` could no longer early-return on `org is None`). For a
migration that has to touch every one of 58 tables in one shot, Option B is
strictly lower-risk: one predicate change, no new contract for future code to
violate, and it composes with the existing `e6a8c0b2d4f6` users-table
precedent without a second special case.

**Known limitation, accepted:** Option B does not make the underlying Postgres
GUC quirk go away — it engineers around both values it can produce (`NULL` and
`''`) rather than explaining or eliminating the quirk itself. This ADR is the
record so a future reader doesn't have to rediscover the reproduction. If a
THIRD sticky value is ever discovered (none is known; the reproduction above
was run exhaustively across COMMIT/ROLLBACK/RESET/explicit-NULL), it would
need the same treatment.

## Consequences
- **Fail-closed preserved.** The fix only ADDS a value to the "pass all rows"
  branch that Postgres's own GUC semantics already produce as the *only*
  reachable non-NULL "unset" state; a real org id can never be the empty
  string (org ids are UUIDs), so no cross-tenant row becomes newly visible.
  The scoped path (`org_id::text = current_setting(...)`) is byte-identical to
  before — no behaviour change to the overwhelming majority of requests.
- **`tests/test_rls.py`'s existing cross-tenant probes stay green, unmodified**
  (`test_rls_blocks_cross_tenant_raw_query`,
  `test_rls_users_visibility_is_membership_driven`) — proof the isolation
  guarantee is identical before and after.
- **New regression coverage** (`tests/test_rls_connection_reuse.py`):
  1. Two sequential authenticated HTTP requests on a `pool_size=1` engine —
     fails on pre-fix code (second request 401s with a perfectly valid token),
     passes post-fix, run 4× to rule out an ordering fluke.
  2. A genuinely-unscoped query (org=None throughout, the worker-claim /
     platform-operator shape), run after a scoped request on the same
     `pool_size=1` engine, exercising the real `app.core.tenant` machinery
     (not a raw-SQL simulation) — asserts it still sees every org's rows, not
     zero. This is the other half of the bug (unscoped paths losing
     visibility, not just re-authentication).
- **`tests/test_credit_note_lock_concurrency.py` retrofitted** off `NullPool`
  onto a normal `pool_size=2` (concurrency needs two simultaneous
  connections) pooled engine, and still passes reliably (4/4 runs) — proof the
  fix, not the workaround, now protects that test.
- **SQLite unaffected.** `_sync_rls_org`/`apply_db_tenant` are no-ops off
  Postgres; RLS itself doesn't exist there. Full SQLite suite green,
  unchanged pass count plus the new tests (which `skipif` off SQLite).
- **Rollback:** the migration is a straight policy-SQL replacement (no
  data/column change); `alembic downgrade -1` restores the prior predicates
  verbatim (round-trip tested: `downgrade -1` → `upgrade head` →
  `alembic check` clean, policy SQL diffed via `pg_get_expr` before/after).
  Revert is low-risk from a security standpoint even under time pressure —
  the bug is a hiding failure, not a leak.

## Revisit when
A third sticky GUC value is ever observed (none known); or if `app/core/tenant.py`
is ever restructured to use an app-side sentinel for an unrelated reason, at
which point this ADR's "known limitation" section should be revisited alongside
it.
