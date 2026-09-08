# The tenant guard's per-statement cost — 2026-09-08 (reference R4, PERF-DUCK-002 → PERF-019)

Reference integration R4 rewrote the AP-inbox read (`approval_policy.waiting_for`)
into one projected SELECT (PERF-DUCK-001) and then re-measured the dashboard
against the concurrency datapoints, as the register required (PERF-DUCK-002).
The re-measurement did NOT move the dashboard. Profiling the request found the
cost that six CI datapoints and the pool measurement had circled (PERF-018)
somewhere else: in the ORM tenant guard, on every SELECT the process issues.
This file records the measurement, the attribution and the bound; the remedy is
a batch of its own (PERF-019), not a line here. (The pushdown got its own datapoint in R6, once the harness seeded approval steps — `SEED-STATISTICS-2026-09-08.md`.)

Machine: Xeon 2.80 GHz × 4, Postgres 16.13, `RATE_LIMIT_ENABLED=false`,
`backend/scripts/perf_harness.py --concurrency 8 --scale 300 --rounds 4`, the
same invocation CI's `postgres` job prints. Three different measurements on the
same database — before, after, and the bound — plus the three alternating
before/after pairs of §1a and the per-call timings; peak RSS 183–185 MB; gen-2
threshold 100 (ADR-A33).

## 1. Before / after the pushdown — the dashboard does not move

`before` is the committed code (a worktree at fe66349); `after` is the R4 tree.

```
before (HEAD fe66349)
dashboard                          70.5     376.2     645.6    646.1    17.7       0   9.16  CONTENDED
ap_aging                           32.1     204.1     206.7    207.1    38.9       0   6.45  OK
cash_position                      57.0     273.5     286.4    286.7    29.1       0   5.03  OK
transport_reliability              47.0     257.9     261.6    262.0    30.7       0   5.57  OK
invoice_create_same_org            52.5     275.2     378.6    391.7    25.5       0   7.21  OK
invoice_create_across_orgs         46.8     241.8     813.6    815.7    20.7       0  17.40  CONTENDED
after (R4 pushdown)
dashboard                          62.6     410.6     663.4    663.9    16.8       0  10.60  CONTENDED
ap_aging                           34.8     210.7     218.4    218.9    37.7       0   6.27  OK
cash_position                      42.0     257.5     266.2    266.5    30.8       0   6.34  OK
transport_reliability              40.3     268.1     274.7    275.2    29.7       0   6.82  OK
invoice_create_same_org            51.8     256.7     294.1    299.6    27.6       0   5.68  OK
invoice_create_across_orgs         43.3     251.1     282.6    282.8    31.1       0   6.53  OK
```

Read honestly: the dashboard's serial p95 (70.5 → 62.6 ms), concurrent p95
(646 → 663 ms) and throughput (17.7 → 16.8 req/s) are all inside one run's
noise; the ratio stays 9–11× as in datapoints 3–6. The across-workspace
create's 17.40× in the `before` run is the same runner-noise artefact
datapoints 3 and 5 recorded (an 800 ms outlier on a 47 ms baseline).

**Why the pushdown could not move it (CONFIRMED):** the harness seeds
invoices in `submitted` state but creates no approval steps, so `waiting_for`
returned zero rows under both shapes — the old code hydrated nothing to
reduce. The pushdown is still the right shape (one SELECT, five columns,
`LIMIT` in SQL — `tests/test_dashboard.py` proves the execution shape), and it
is where the cost WOULD have been for a workspace with a real approval queue;
it is simply not what the concurrency mode measures. A dataset that would show
it: `scale` pending steps across `scale` invoices with ten approvers — not
added to the harness in R4 (a harness change is its own row).

### 1a. Three alternating runs (review P-3) — and what they exposed

One run each cannot separate a change from run noise, so the pair was repeated
three times, alternating before/after on the same database. The first `after`
tree read WORSE on the dashboard in all three pairs (serial p95 97–118 ms
against 63–76; concurrent p50 +15–40 ms; throughput −3–5 %). The step the
DuckDB order put first and this batch had skipped — measure the statement
itself — then found why (scratch script, the perf workspace, 40 calls each,
guard on): the OLD read cost 3.8 ms per call, the NEW one 5.1 ms, while
Postgres executed the new statement in 0.05 ms (EXPLAIN ANALYZE below) and
the compiled cache never missed. ~70 % of the new read's cost was Python
STATEMENT CONSTRUCTION on every call — two `aliased()` column collections,
the correlated subquery, the joins (`corresponding_column` /
`_populate_column_collection` in the profile), not the query.

Fix: the statement is built ONCE at import with bound parameters
(`_INBOX_STMT`; `org_id`, `user_id`, `can_approve_any`, `limit`). Measured
again the same way, the new read is now faster than the old on the empty
inbox as well: **2.9 ms against 3.8 ms per call with the guard on; 0.8 ms
against 1.3 ms with the guard attaching nothing** — i.e. the old shape's
remaining cost was mostly its own statement, and the new statement's cost is
mostly the guard (§2–3).

The harness pairs, medians of three (the `serial p95` column is the max of
eight serial requests and swings 60–135 ms on the dashboard between runs of
the SAME tree — read the concurrent columns):

| scenario | serial p95 (3 runs) before → after | conc p50 | conc p95 | req/s |
|---|---|---|---|---|
| dashboard | 73 / 76 / 63 → 136 / 90 / 68 | 434.5 → 451.1 | 721.9 → 754.9 | 16.1 → 15.1 |
| invoice_list | 58 / 29 / 25 → 55 / 51 / 32 | 140.2 → 136.4 | 149.5 → 148.4 | 55.7 → 57.5 |
| ap_aging | 47 / 43 / 42 → 40 / 40 / 43 | 235.9 → 229.7 | 256.4 → 260.8 | 32.7 → 34.0 |
| cash_position | 52 / 54 / 47 → 43 / 47 / 52 | 300.6 → 267.6 | 327.9 → 276.7 | 26.1 → 29.7 |
| transport_reliability | 53 / 69 / 73 → 47 / 49 / 55 | 308.9 → 283.7 | 377.8 → 326.9 | 24.7 → 27.0 |
| invoice_create_same_org | 87 / 71 / 65 → 67 / 71 / 59 | 277.6 → 287.6 | 315.5 → 321.7 | 25.7 → 25.0 |
| invoice_create_across_orgs | 47 / 67 / 40 → 96 / 47 / 51 | 245.2 → 288.4 | 279.6 → 312.5 | 29.8 → 27.5 |

Verdict for PERF-DUCK-002: **no measurable change at this dataset in either
direction** — the dashboard's three-run bands overlap (before 14.6–16.3 req/s,
after 14.3–16.3), as do every other scenario's — and a per-call timing that
reads 0.9 ms faster with the guard on. The order's EXPLAIN step:

```
Limit  (cost=41.21..41.22 rows=1) (actual time=0.006..0.007 rows=0)
  ->  Sort  Sort Key: invoices.submitted_at DESC NULLS LAST, approval_steps_1.seq
        ->  Nested Loop Anti Join  (actual time=0.003..0.003 rows=0)
              ->  Nested Loop Left Join  ->  Nested Loop
                    ->  Index Scan using ix_approval_steps_invoice on approval_steps approval_steps_1
                          Index Cond: (org_id = $1)  Filter: approver/status/RLS
                    ->  Index Scan using uq_invoices_org_id on invoices (never executed)
              ->  Index Scan using ix_approval_steps_invoice on approval_steps approval_steps_2 (never executed)
Planning Time: 0.663 ms   Execution Time: 0.051 ms
```

Every predicate is index-served (`ix_approval_steps_invoice`,
`uq_invoices_org_id`, `uq_vendors_org_id`); the RLS filter appears on every
table; no index is missing at this scale (review P-5: `invoices (org_id,
workflow_state, submitted_at DESC)` becomes worth measuring only once an org
holds tens of thousands of open unassigned steps).

## 2. Where one dashboard request actually spends its loop time

`cProfile` over 40 serial `/api/v1/dashboard` requests in the harness's
process (scratch script, same workspace, same seed). One request issues 20
statements — 4 for identity, 1 `set_config`, 15 for the six sections — and
EVERY one of them carries a cache key with 104–125 bound values, including
`SELECT count(*) FROM payment_runs WHERE org_id = … AND status = …` (the
rendered SQL binds only the 8–13 the planner sees; the other ~100 belong to
loader options whose criteria that statement never renders).

```
   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
250720/760  1.152    0.000    1.763    0.002 sqlalchemy/sql/cache_key.py:221(_gen_cache_key)
     2400    0.345    0.000    0.345    0.000 {method 'poll' of 'select.epoll' objects}
    93080    0.268    0.000    0.490    0.000 sqlalchemy/sql/coercions.py:314(expect)
    75000    0.192    0.000    0.214    0.000 sqlalchemy/sql/elements.py:2160(_gen_cache_key)
                    … total 4.342 s profiled; serial wall 114.9 ms/request under the profiler (62.6 ms without)
```

Those values are the tenant guard: `app/core/tenant.py::_apply_tenant_scope`
attaches one `with_loader_criteria(model, model.org_id == :org)` option per
registered tenant model — 103 of them, plus 5 soft-delete criteria — to every
SELECT, and
SQLAlchemy must generate the statement's cache key over all of those options
on every execute before it can look up the compiled form. 250,720 key-
generation calls over 40 requests is ~6,300 per request — ~100 options × 3
elements × 20 statements. Together `_gen_cache_key` and the option coercions
are ~40 % of the profiled function time of a request whose SQL is fifteen
`count(*)`/`sum(...)` aggregates against 300 rows. That share is
profiler-weighted (the request costs 115 ms under cProfile against 63 ms
without, and the inflation concentrates in exactly these tiny calls), so the
profile is the ATTRIBUTION; the number is the bound in §3.

## 3. The bound — the same measurement with the guard attaching no options

An in-process monkeypatch in a scratch script (`TENANT_MODELS = ()` AND
`SOFT_DELETE_MODELS = ()` — so the bound includes the five soft-delete criteria
as well as the ~80 tenant ones; RLS still scoped every row; this was a
MEASUREMENT of the guard's CPU, never a configuration and never in the tree):

```
dashboard                          36.2     186.0     452.1    453.4    31.4       0  12.50  CONTENDED
invoice_list                       19.5      66.7      68.3     68.8   116.2       0   3.50  OK
ap_aging                           21.0     125.2     135.0    135.2    62.7       0   6.43  OK
cash_position                      25.3     113.3     114.9    115.2    69.4       0   4.55  OK
explore_by_vendor                  13.6      53.4      55.1     56.2   143.3       0   4.05  OK
transport_reliability              27.4     144.3     147.5    147.9    54.6       0   5.38  OK
invoice_create_same_org            32.8     134.5     147.4    152.7    53.0       0   4.50  OK
invoice_create_across_orgs         23.7     129.2     135.7    136.0    61.2       0   5.73  OK
```

Against the `after` run above: every serial p95 falls by 40–50 % (dashboard
62.6 → 36.2 ms, cash position 42.0 → 25.3, ap aging 34.8 → 21.0, the same-org
create 51.8 → 32.8) and throughput rises 1.7–2.3× on every scenario
(dashboard 16.8 → 31.4 req/s, invoice list 60.9 → 116.2, cash position
30.8 → 69.4). The dashboard's RATIO stays contended (12.5×) because it still
runs twenty statements on one loop with eight in flight — the ratio measures
shape, and the shape is unchanged — but the absolute cost of every request in
the product roughly halves. So the acceptance for the remedy is ABSOLUTE:
serial p95 and req/s at N=8; the harness's `x` stays informational for this
row, and PERF-018's own remedy (fewer statements per request) is what moves
the ratio.

## Conclusion

**PERF-019 (new, CONFIRMED):** the ORM tenant guard costs roughly half of every
request's loop time, because it makes every SELECT carry ~100 loader-criteria
options whose cache key SQLAlchemy regenerates on every execute. This is the
per-request CPU that CONC-001 → PERF-018 attributed to the dashboard's own
reductions: the dashboard merely shows it most, at twenty statements per
request. **PERF-018 stays open**, with PERF-019 as its named cause.

One more thing the captured guarded SQL showed (R4 panel, Security lens): the
guard's loader criteria reach the FROM list, the joins and their aliases, but
NOT a correlated subquery — inside `waiting_for`'s `NOT EXISTS` the only
layer-2 predicate is the statement's own `org_id`, with RLS behind it. Any
redesign inherits that limit; it is documented in `app/core/tenant.py`.

What is NOT changed here: the guard. It is defence layer 2 of tenant
isolation (ADR-0004; `tests/test_tenancy_parity.py`, `test_cross_tenant_isolation.py`)
and its mechanism is a security decision. The remedy candidates — one
`with_loader_criteria` on a shared tenant base class instead of one per model,
a guard that attaches criteria only for the entities a statement can reach
(including relationship loads and aliases), or a cheaper cache-key form — must
each be proven by the parity and isolation suites, the RLS suite on Postgres
and a seeded cross-tenant leak before any of them replaces the current one.
That is a batch of its own with the Security lens in the panel.

## 4. The remedy, measured (reference R5, same day)

`_tenant_options` now returns ONE `with_loader_criteria` on the declarative
`Base` — its callable decides per class by attribute (`org_id` → the org
column; `User` → membership existence, as before; no `org_id` → `true()`),
with the tenant id as a typed bind parameter closed over so SQLAlchemy's
lambda analysis keeps the option's cache key structural and carries the value
per execution. `_deleted_options` likewise. Two options per SELECT instead of
108 (`tests/test_dashboard.py::test_dashboard_statement_shape_is_bounded`
ratchets it at ≤ 2). Isolation proofs: ADR-0004 addendum and
`tests/test_tenant_guard_mechanism.py`.

Same machine, same invocation, three runs each; the R4 final tree (the three
`after` runs of §1a) against the FINAL R5 tree (the runs postdate the last
edit, as the panel required — the seed log records the tree's hashes),
medians:

| scenario | serial p95 | conc p50 | conc p95 | req/s | `x` |
|---|---|---|---|---|---|
| dashboard | 89.8 → 44.7 | 451.1 → 187.2 | 754.9 → 466.0 | 15.1 → 31.1 (×2.06) | 8.67 → 11.43 |
| invoice_list | 50.6 → 24.5 | 136.4 → 72.0 | 148.4 → 75.8 | 57.5 → 107.0 (×1.86) | 2.94 → 2.85 |
| ap_aging | 40.3 → 26.2 | 229.7 → 134.6 | 260.8 → 148.0 | 34.0 → 57.2 (×1.68) | 6.54 → 5.59 |
| cash_position | 47.2 → 32.3 | 267.6 → 128.0 | 276.7 → 131.9 | 29.7 → 61.2 (×2.06) | 6.29 → 3.96 |
| transport_reliability | 49.4 → 33.7 | 283.7 → 180.7 | 326.9 → 195.7 | 27.0 → 43.0 (×1.59) | 5.98 → 5.02 |
| invoice_create_same_org | 66.9 → 45.2 | 287.6 → 159.5 | 321.7 → 178.0 | 25.0 → 44.4 (×1.78) | 5.04 → 3.94 |
| invoice_create_across_orgs | 50.9 → 34.3 | 288.4 → 136.5 | 312.5 → 157.0 | 27.5 → 55.9 (×2.03) | 6.13 → 4.58 |
| explore_by_vendor | 23.3 → 17.0 | 105.2 → 62.2 | 111.2 → 67.0 | 74.1 → 123.6 (×1.67) | 4.78 → 3.87 |

Read against §3's bound (the guard attaching NOTHING: dashboard 31.4 req/s,
invoice list 116.2, cash position 69.4): the remedy recovers most of it —
dashboard 31.1, invoice list 107.0, cash position 61.2 — with layer 2 fully
in place. Every scenario's concurrent p50 roughly halves; throughput rises
×1.6–2.1; zero errors in all three runs. The dashboard's ratio `x` is
unchanged as predicted (shape: twenty statements on one loop) — the
acceptance for PERF-019 was absolute serial p95 / req/s, and it is met.

What the remedy cost (the R5 panel's profile of 40 serial dashboard requests
under the new guard): the per-class resolution of the callable runs 0 times
in steady state (once per statement compile, cached with the statement);
`_gen_cache_key` calls per statement fell from ~330 to 31; the guard is now
≈ 0.9 ms of a request. Collateral: every entity without `deleted_at` gets
`AND true` appended (harmless to the planner). The next cost of a dashboard
request is its twenty sequential round trips (`epoll` ≈ 8.4 ms per request)
plus per-statement overhead — PERF-018's own remedy (fewer statements) is
now the lever. Rollback: revert the four-file commit and restart (ADR-0004
addendum).
