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
| 2026-09-06 | CI `postgres` job, run pending | 300 | 8 | first run — the table below is filled from the job log once the run completes |

_(The first CI datapoint is appended here in the commit that reads it.)_

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
