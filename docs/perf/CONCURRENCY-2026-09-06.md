# Concurrency measurement — 2026-09-06 (audit 2026-09-05, "concurrency and write-path performance unmeasured")

The read-path baseline (`BASELINE-2026-08-27.md`) measured every endpoint one
request at a time. The audit's final report recorded what that leaves unknown,
verbatim: *"concurrency and write-path performance unmeasured"*. This closes
the gap the same way the read path was closed: with a mode of the same harness,
against the same migrated Postgres, printed on every CI run so a number that
drifts is seen before it matters.

## What is measured

`backend/scripts/perf_harness.py --concurrency N` drives the **real ASGI app**
through the **real router stack** in ONE process, ONE event loop, ONE connection
pool — the shape of a single uvicorn worker under load. For every read
scenario of the baseline and for two write scenarios it measures:

| column | meaning |
|---|---|
| serial p95 | N requests one after another, nearest-rank p95 |
| conc p50 / p95 / max | `rounds` × N requests with N in flight at once (`asyncio.gather`) |
| req/s | requests completed per wall-clock second during the concurrent rounds |
| errors | any 4xx/5xx under load — never hidden: a 500 under load is the finding this mode exists to surface |
| x | concurrent p95 ÷ serial p95 |

The two write scenarios are the POST the product exercises most, one supplier
invoice with one line (`POST /invoices`, the same route the batch-upload confirm
uses):

- **`invoice_create_same_org`** — N invoices created at once in ONE workspace:
  the contention case (same tenant, same audit hash chain, same vendor row
  resolved by name).
- **`invoice_create_across_orgs`** — N workspaces, one invoice each at once:
  the multi-tenant steady state, where nothing should contend.

## How to read the verdict

The verdict is a **ratio, not a millisecond budget**, for the reason the read
gate is a ratio: a shared CI runner inflates both halves and leaves the ratio
alone. With N in flight on one event loop the ideal is `x ≈ N` for CPU-bound
work (the requests take turns) and `x < N` for I/O-bound work (they overlap).
A scenario is **CONTENDED** above `x = N`: the requests interfered with each
other beyond merely queueing — lock waits, pool starvation, a serialised
chain — and concurrency made things worse than a queue would have. **ERRORS**
is any non-2xx under load.

The mode is **informational** in CI (printed by the `postgres` job on every
run, never gated). A ceiling will be set from the first datapoints on the real
host, exactly as the growth ceilings were; until then the datapoint IS the
deliverable. The one hard failure is an error under load.

## Running it

```
cd backend
DATABASE_URL=postgresql+asyncpg://user:pw@host:port/db \
  python scripts/perf_harness.py --concurrency 8 --scale 300 --rounds 4 --json /tmp/concurrency.json
```

It refuses SQLite, as every measurement mode does: SQLite serialises every
writer, so a same-tenant write measurement there would describe an engine the
product never runs on.

## Datapoints

| date | where | scale | N | notes |
|---|---|---|---|---|
| 2026-09-06 | CI `postgres` job, run #543 (GitHub-hosted 4-vCPU runner, Postgres 16 service container) | 300 | 8 | **datapoint 0 — two artefacts, then real numbers.** Recorded verbatim below because the artefacts are themselves findings. |

### Datapoint 0 — CI #543, verbatim

```
scenario                     serial p95  conc p50  conc p95      max   req/s  errors      x  verdict
----------------------------------------------------------------------------------------------------
dashboard                          47.6     307.2     594.8    595.0    19.4       0  12.50  CONTENDED
invoice_list                       19.2      94.7      96.4     96.7    82.7       0   5.03  OK
ap_aging                           27.9     164.1     281.9    282.0    41.0       0  10.12  CONTENDED
cash_position                      31.6     199.7     327.5    327.8    34.3       0  10.35  CONTENDED
explore_by_vendor                  17.7      77.8      81.5     82.1    97.8       0   4.60  OK
transport_reliability             174.2     202.1     350.6    351.0    33.3       0   2.01  OK
invoice_create_same_org            14.7      82.2      83.6     84.3    94.8      32   5.71  ERRORS
invoice_create_across_orgs         13.2     192.9     237.2    237.8    38.9       0  17.94  CONTENDED
```

The job log's own status counts for `POST /invoices`: **201 × 32, 402 × 48,
429 × 4.** Two things measured that were not latency:

1. **402 — the plan cap.** The harness registers its workspace on the
   `trial` plan, whose monthly invoice cap is **10**; the seeded rows plus
   the warm-up and serial creates exhausted it before the concurrent rounds,
   so every same-workspace concurrent create — and the across-workspace
   SERIAL baseline, which posts from the same first workspace — answered 402
   in a few milliseconds. That is the product working (PROD-004's door, now
   with the owner's upgrade path), not a write-path failure; but it makes the
   `invoice_create_*` rows of this datapoint **void**: a 402 is fast, so the
   serial p95 it produced is not a baseline. Fixed in the harness: the perf
   workspace's plan is uncapped in the measurement database.
2. **429 — the per-token general rate limit.** `rate_limit_per_min` is 300
   per token/IP in a fixed 60-second window; the harness fires roughly 340
   requests from ONE token inside a minute, so the tail of the run tripped
   it. For the measurement that is noise (the CI step now runs with the
   limiter off, and says so); for the product it is a datapoint worth
   keeping: **one API token cannot sustain more than 5 requests/second**,
   which an integration replaying a day's invoices will meet. Recorded as
   a finding for the owner's judgement (it is a policy number, not a
   defect).

What the datapoint DOES say, for the read scenarios, which had no errors:

- `invoice_list`, `explore_by_vendor` and `transport_reliability` scale
  as I/O-bound work should — 8 in flight cost 2–5× the serial p95, i.e. the
  requests overlapped.
- `dashboard` (12.5×), `ap_aging` (10.1×) and `cash_position` (10.4×) cost
  MORE than serialising them would — the CONTENDED verdict. These three are
  the aggregate reads PERF-002/003 moved into SQL; at 8 in flight on one
  event loop they now contend on something beyond CPU turn-taking: the
  connection pool (SQLAlchemy's default 5 + 10 overflow against 8 in
  flight, each holding a connection for a multi-statement transaction) is
  the first suspect, lock waits on the shared `usage_counters`/audit rows
  the second. **One datapoint on a shared runner does not settle it**; it
  names the next measurement (pool size as a variable) and is logged as a
  finding, not a conclusion.

The next green CI run supplies datapoint 1 with both artefacts removed; the
write-path rows become meaningful from there.

## What the smoke run on SQLite showed, and why it is recorded

Before the mode ever ran on Postgres it was exercised once on a scratch SQLite
file, numbers discarded, purely to prove the code path. It surfaced one thing
worth keeping: with N same-tenant invoice creates in flight, one of them
failed with `UNIQUE constraint failed: audit_events.org_id, audit_events.seq`
and answered 500 — the audit-trail insert (the per-organisation hash chain
with its `seq`) is where concurrent writers of one tenant meet.

That is a **SQLite artefact, not a product defect**: `audit.record` serialises
per-tenant appends with `pg_advisory_xact_lock(hashtext('audit:<org>'))` on
Postgres, so two writers read the chain head one after the other; SQLite has
no advisory locks and the guard is a no-op there (the same reason every
concurrency test in `tests/` is Postgres-only). What the Postgres measurement
of `invoice_create_same_org` therefore shows is the **cost** of that lock —
how far N same-tenant writes fall short of overlapping — not whether they
survive. The harness counts errors under load instead of raising on the first
one precisely so a real 500 on the real engine would be a line in the table
rather than a crashed run.
