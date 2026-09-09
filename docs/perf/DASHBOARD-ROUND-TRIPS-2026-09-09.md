# PERF-018 — the composed dashboard's round trips (2026-09-09)

CONC-001 named one endpoint whose concurrent p95 sat far above a queue's, and
the pool measurement (P2 batch 4) refuted the pool: the cost is the request's
own work on one event loop. PERF-019 then took out the absolute half of it (the
tenant guard's per-model loader criteria, R5). What was left for this row was
its own remedy — **fewer round trips per request** — and this file is the
measurement that closed it.

## What the profile actually found

Not "a few statements too many". With the `issuing` module ON — every workspace
that raises sales invoices — one `GET /api/v1/dashboard` ran **24 SELECTs**, of
which eight were work whose result was discarded before the response was built:

| Read | Statements | Rendered? |
| --- | ---: | --- |
| Canonical receivables report, for the receivables card | 4 | yes, 4 figures |
| The SAME report again, inside `cash_position.summary` | 4 | no — one figure survived |
| AR aging bands (part of each of the two above) | 2 of the 8 | no |
| Bank-reconciliation roll-up (`bank_lines`) | 2 | no |
| Payables: other-currency list | 1 | no |

The dashboard called `cash_position.summary()` — the full cash-cycle roll-up
that the analytics route publishes — for **four** of its figures, and that
roll-up reads the receivables report itself. `issued_reports.receivables_scalars`
has carried the comment "(and cash position, a second time in the same request)"
since PERF-003 in the 2026-09-05 audit; nothing acted on it.

### Why no measurement had seen it

`issuing` is default-off and the perf harness enabled only `transport`. Every
run since PERF-003 measured a dashboard with its receivables card skipped —
the one shape in which the duplicate does not happen. Same class of blind spot
as PERF-004 (no payable state seeded) and PERF-DUCK-001 (no approval steps).
The harness now enables `issuing` on its workspace; it already seeded
`issued_invoices`, so the module was the only thing in the way.

## The change

* One canonical AR read per request, shared by the cash card and the
  receivables card, and only when a card that shows it survived the permission
  gates.
* `receivables_scalars(..., with_aging=False)` skips the band query for the two
  callers that render no bands. `aging` is then `None`, never `[]` — a caller
  that forgets which it asked for gets a `TypeError`, not a screen of confident
  zeros.
* `cash_position.net_figures()` answers the four figures the card shows.
  `summary()` is untouched for the analytics route; both take their payables
  from ONE `_ap_row` definition, so there is no second copy of the arithmetic
  to drift.

**24 → 16 statements**, and the numbers are proven identical to the full
roll-up's, field by field, on the PERF-002 dataset (every lifecycle, credit
notes, partial payments, several currencies).

## Measured

Local Postgres 16, scale 400, one uvicorn worker, quiet machine, `issuing` ON
in both halves — the "before" runs come from a detached worktree at the parent
commit with only the harness change applied, so the two halves differ by the
service change alone.

Serial, 60 reps (`--scale 400`):

| | dashboard p50 | dashboard p95 |
| --- | ---: | ---: |
| before, run 1 | 43.3 ms | 45.5 ms |
| before, run 2 | 45.9 ms | 50.1 ms |
| after | 32.4 ms | 36.5 ms |

Eight requests in flight (`--concurrency 8`), the row's declared KPI:

| | serial p95 | conc p50 | conc p95 | req/s |
| --- | ---: | ---: | ---: | ---: |
| before, run 1 | 81.8 ms | 218.4 ms | 345.3 ms | 31.8 |
| before, run 2 | 47.0 ms | 217.7 ms | 401.9 ms | 30.6 |
| after, run 1 | 32.2 ms | 139.4 ms | 301.3 ms | 44.2 |
| after, run 2 | 31.5 ms | 129.8 ms | 311.9 ms | 45.6 |

**Throughput 30.6–31.8 → 44.2–45.6 req/s (+40 %)**, serial p95 down about a
quarter to a half depending on the run. Zero errors in every run. No other
scenario moved: only the dashboard's composition changed.

### The `x` column, honestly

`x` is concurrent p95 ÷ serial p95, and the harness prints CONTENDED above 8.
The two before-runs disagree with each other about it, because they disagree
about the denominator:

| run | serial p95 | conc p95 | `x` |
| --- | ---: | ---: | ---: |
| before, run 1 | 81.8 ms | 345.3 ms | 4.22 |
| before, run 2 | 47.0 ms | 401.9 ms | 8.55 |
| after, run 1 | 32.2 ms | 301.3 ms | 9.37 |
| after, run 2 | 31.5 ms | 311.9 ms | 9.90 |

Run 1's serial p95 of 81.8 ms is an outlier against every other serial
measurement on this machine (45.5, 50.1, 47.0, 36.5) and it is the number that
produces the flattering 4.22. Quoting "4.2 → 9.4" would be choosing the
before-run that maximises the apparent regression and then explaining it away.
**Like for like, `x` goes 8.55 → 9.37–9.90: essentially flat**, while the
numerator falls (402 → 301 ms) and throughput rises 40 %.

That flatness is the expected result, not a lucky one. `x` is a SHAPE property
— a dozen-odd statements per request on one event loop with eight in flight —
and this row's acceptance was recorded in advance as **absolute serial p95 and
req/s, never `x`**. PERF-019 measured the same floor from the other side: with
the tenant guard attaching nothing at all, the dashboard still read 12.5× by
shape alone.

R15 growth gate after the change, 1,200 → 4,800 rows: dashboard **1.64** against
a 4.0 ceiling (p50 1.67); every other scenario unchanged and inside its ceiling.

## What was deliberately NOT done

The remaining sixteen are four per-request context reads (user, session,
membership, organization) and twelve section reads. Three of the twelve are
single indexed `count(*)`s from three different canonical services, and one more
pair is the extraction review-queue summary. They could be folded into one
round trip as scalar subqueries — and that is exactly why they were not: the
ORM tenant guard (isolation layer 2) reaches a statement's FROM list, joins and
aliases but **not inside a correlated subquery** (measured in
`TENANT-GUARD-2026-09-08.md` §3). Folding four canonical counts into subqueries
would move them out of layer 2's reach to save four cheap indexed counts, leaving
their explicit `org_id` filter and RLS. Trading an isolation layer for four
round trips is not a trade this row is worth. A short-lived per-tenant cache of
the composed figure — the row's other suggested remedy — stays available if the
endpoint ever needs more than this.
