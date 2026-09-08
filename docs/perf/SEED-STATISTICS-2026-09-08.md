# The gate measured a plan production does not run — 2026-09-08 (reference R6, PERF-020)

Machine: the same local Xeon 2.80 GHz × 4, Postgres 16.13, as every 2026-09 note.
Harness: `backend/scripts/perf_harness.py --shape --scale 2000` (2,000 → 8,000 rows per
fact table) and `--concurrency 8 --scale 300 --rounds 4`, three runs.

## 1. What changed in the seed, and what the first run read

Reference R5's follow-up (P-4) gave the harness what it had never seeded: one generic
pending approval step per submitted invoice, exactly what `build_chain` creates when a
workspace without approval policies presses Submit. Until then the dashboard's AP inbox
(`approval_policy.waiting_for`, the PERF-DUCK-001 pushdown) reduced an EMPTY set at
every scale, so the pushdown had no `--shape` datapoint of its own.

The first `--shape` run with the new seed:

| scenario | small p95 | large p95 | ratio | ceiling | verdict |
|---|---|---|---|---|---|
| dashboard | 82.4 ms | 666.1 ms | **8.08** | 4.0 | GREW TOO FAST |
| invoice_list | 17.4 | 32.1 | 1.84 | 5.0 | OK |
| ap_aging | 25.1 | 38.7 | 1.54 | 4.0 | OK |
| cash_position | 30.8 | 62.0 | 2.01 | 4.0 | OK |
| explore_by_vendor | 17.2 | 30.8 | 1.79 | 9.0 | OK |
| transport_reliability | 30.6 | 79.2 | 2.59 | 8.0 | OK |

## 2. Where the 666 ms went — one statement, one plan

Per-statement cursor timing of five serial `/dashboard` requests at scale 8,000
(`probe_dashboard_stmts.py`, EXPLAIN ANALYZE of the slowest): 799 ms of SQL per request
over twenty statements, **740 ms of it the inbox SELECT**. Its plan:

```
Nested Loop Anti Join  (rows=1 estimated, 1067 actual)  actual 630 ms
  Join Filter: earlier_pending.seq < current_step.seq AND earlier_pending.invoice_id = current_step.invoice_id
  Rows Removed by Join Filter: 1,138,489        Buffers: shared hit 1,096,829
  -> Index Scan ix_approval_steps_invoice (current_step)   Index Cond: org_id = …   rows=1 est, 1067 actual
  -> Index Scan ix_approval_steps_invoice (earlier_pending) Index Cond: org_id = …   loops=1067
```

The planner estimated ONE pending step for the tenant — the statistics on `approval_steps`
dated from when the table was empty — so it chose a nested-loop anti join whose inner scan
uses only `org_id` of the `(org_id, invoice_id)` index and filters the invoice in Python-ish
fashion: 1,067 × 1,067 rows. The statement, the index and the `NOT EXISTS` shape are not
the defect; the estimate is.

`ANALYZE approval_steps, invoices, vendors` and the same five requests: **84 ms per request,
66 ms of SQL; the inbox statement fell below 2 ms** and out of the top eight. The anti join
becomes a parameterised index lookup per step.

## 3. Why the harness — not the statement — is what changed

Production is not in the unanalysed state for long: autovacuum analyses a table after
50 rows + 10 % of it change (the first tenant to use approvals trips that within a minute),
and a tenant the statistics have not seen is estimated from the distinct-count of the ones
they have — hundreds of rows, enough to choose the lookup plan. A bulk seed into a table
analysed when empty is the harness's situation only: CI's `postgres` job seeds the gate's
two workspaces back to back and measures at once, and whether autovacuum's naptime falls
between the seed and the measurement is a coin toss — exactly the flakiness the R15 gate
has bitten this project with before (PERF-016). `_prepare_workspace` therefore runs
`ANALYZE` over the seven seeded tables after seeding (Postgres only; `_analyze_after_seed`),
so the gate measures the steady-state plan. A Postgres-engine test in
`tests/test_perf_harness_seed.py` (CI's `postgres` job) proves the seeded tables carry a
fresh `last_analyze` afterwards.

Rewriting the statement to be statistics-independent (a `MIN(seq)` grouping instead of the
correlated `NOT EXISTS`) was considered and NOT done: no measured production condition asks
for it, the current shape is the right one once statistics exist, and R4's differential
proof of the pushdown would have to be redone. If a production tenant ever shows the
nested-loop plan, that is the change to make — recorded on PERF-020.

## 4. The gate and the concurrency run with the analysed seed (final tree, three runs)

`--shape`, 2,000 → 8,000:

| scenario | small p95 | large p95 | ratio | ceiling | p50 ratio |
|---|---|---|---|---|---|
| dashboard | 45.9 ms | 89.2 ms | **1.94** | 4.0 | 1.96 |
| invoice_list | 17.1 | 15.6 | 0.91 | 5.0 | 1.32 |
| ap_aging | 25.4 | 39.7 | 1.56 | 4.0 | 1.57 |
| cash_position | 31.5 | 64.8 | 2.06 | 4.0 | 2.14 |
| explore_by_vendor | 16.4 | 33.7 | 2.05 | 9.0 | 2.13 |
| transport_reliability | 33.2 | 82.2 | 2.48 | 8.0 | 2.52 |

The dashboard now carries the inbox's rows (1,067 pending steps at 8,000 invoices; LIMIT
10 with the correlated `NOT EXISTS` and the newest-first sort) and grows 1.94× across 4× of
data — the first datapoint the PERF-DUCK-001 pushdown has of its own. Even the small
scale is slower than before the seed (45.9 vs 44.7 ms serial p95 in R5's run at 300 rows is
a different scale; at 2,000 the pre-seed dashboard read 82 ms in §1's first run only because
of the plan — the two figures are not comparable, and neither claims a gain).

Concurrency at 300 rows, 8 in flight, medians of three runs, zero errors:

| scenario | serial p95 | conc p50 | conc p95 | req/s | `x` |
|---|---|---|---|---|---|
| dashboard | 35.8 | 159.5 | 411.6 | 35.1 | 12.08 |
| invoice_list | 21.1 | 58.1 | 67.2 | 131.5 | 3.40 |
| ap_aging | 21.5 | 109.4 | 118.1 | 71.9 | 4.67 |
| cash_position | 25.2 | 104.5 | 105.6 | 75.3 | 4.19 |
| explore_by_vendor | 17.3 | 46.2 | 48.2 | 164.8 | 3.18 |
| transport_reliability | 26.9 | 123.4 | 136.1 | 62.0 | 5.07 |
| invoice_create_same_org | 33.3 | 126.3 | 145.7 | 55.5 | 4.17 |
| invoice_create_across_orgs | 32.9 | 114.0 | 160.6 | 64.8 | 3.75 |

Against R5's final medians (dashboard 31.1 req/s, conc p50 187.2 ms): inside the run bands
with the inbox rows present — at 300 invoices the inbox is ~40 rows and costs nothing
measurable. Not claimed as a gain.

## What is NOT covered

- The statement's behaviour on a tenant whose statistics are stale in PRODUCTION is
  reasoned (§3), not measured; the remedy here is to the measurement.
- The gate still runs on a shared CI runner; the ratio verdict, not the milliseconds, is the
  gate (README).
