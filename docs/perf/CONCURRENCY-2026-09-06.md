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

### Datapoint 1 — CI #544, verbatim (both artefacts removed; zero errors)

```
scenario                     serial p95  conc p50  conc p95      max   req/s  errors      x  verdict
----------------------------------------------------------------------------------------------------
dashboard                          37.1     227.6     423.5    424.4    26.2       0  11.41  CONTENDED
invoice_list                       13.0      70.1      71.1     71.6   111.8       0   5.48  OK
ap_aging                           20.9     122.1     251.3    251.5    51.5       0  12.01  CONTENDED
cash_position                      23.7     150.2     276.3    276.7    43.9       0  11.68  CONTENDED
explore_by_vendor                  13.1      58.1      60.1     60.3   131.9       0   4.58  OK
transport_reliability              27.2     149.2     305.3    305.3    42.3       0  11.22  CONTENDED
invoice_create_same_org            30.0     134.1     148.6    155.9    53.6       0   4.95  OK
invoice_create_across_orgs         22.1     131.2     145.1    145.2    59.0       0   6.57  OK
```

What this one says:

- **The write path holds under load.** Eight invoice creates at once in ONE
  workspace cost 4.95× the serial p95 — the requests overlap despite the
  per-tenant audit-chain advisory lock, and none failed. Across eight
  workspaces 6.57×, none failed. `invoice_create_same_org` ≈ 54 req/s and
  `invoice_create_across_orgs` ≈ 59 req/s on one worker on a shared runner.
- **The aggregate reads contend, reproducibly.** `dashboard` 11.4×,
  `ap_aging` 12.0×, `cash_position` 11.7× — the same three as datapoint 0,
  now joined by `transport_reliability` 11.2× (its 2.0× in datapoint 0 came
  from a cold 174 ms serial baseline; 27 ms here — datapoint 0's reliability
  row was noise). The shape is telling: conc **p50** sits at 5–6× serial while
  conc **p95** sits at 11–12×, i.e. the first requests of each batch of eight
  overlap and the last ones wait for the rest — the signature of a resource
  smaller than eight being handed round. The SQLAlchemy pool defaults to 5
  connections (+10 overflow) and each of these requests holds its
  connection across several statements; `invoice_list` and `explore` return
  in one statement and show no such tail. **CONC-001 stands: measure pool
  size as a variable next, before any code moves.** Two datapoints on a
  shared runner agree on which endpoints, not yet on why.

Ceilings for the concurrency verdict are NOT set from these two datapoints;
the mode stays informational until the pool-size measurement has been made.

**A second suspect, added 2026-09-06 (PERF-016).** Both datapoints above were
measured with the startup heap unfrozen: a full garbage-collection pass over
the imported app cost ~140 ms and is stop-the-world for every request in
flight on the worker, and the four CONTENDED reads are exactly the ones that
allocate most per request. `GC-PAUSE-2026-09-06.md` has the measurement. The
harness now freezes the heap before measuring, as production does at the end
of startup, so **datapoint 2 is the first comparable one with that pause
removed** — if the four ratios fall toward the 5–6× their p50 already shows,
the pool was never the bottleneck; if they hold at 11–12×, it was.

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
