# ADR-0011 — Idempotency keys + guarded/optimistic concurrency

**Status:** Accepted

## Context
At-least-once delivery, retries, concurrent workers, and client re-submits must never cause duplicate financial effects (double invoice, double charge, double email).

## Selected approach
Assume **at-least-once everywhere; make outcomes idempotent**:
- **Content dedup** (SHA-256) for uploaded documents.
- **Job idempotency keys** unique on `(org_id, kind, key)`; re-enqueue while live = no-op.
- **Idempotent handlers**; recurring generation advances `next_run_date` **in the same txn** as invoice creation; the daily scheduler keys jobs by date.
- **Guarded/optimistic concurrency**: atomic `UPDATE ... WHERE status='queued'` for job claim; DB unique constraints as backstops (invoice number per entity, usage counter per period); upsert-with-retry for counters; `flush()` for audit `seq`.
- **Webhook deliveries** carry a delivery id for receiver-side dedup.

## Alternatives considered
- **Exactly-once delivery** — practically unattainable across process/network boundaries; chasing it adds complexity for a guarantee we can get at the *outcome* layer instead.
- **Application-level locks for everything** — contention + deadlock risk; we prefer DB-enforced optimistic controls.
- **Client-supplied idempotency only** — insufficient for server-initiated jobs.

## Why appropriate
Idempotent outcomes are robust to retries, crashes, and duplicate requests without a distributed-lock manager. DB constraints turn "should be unique" into "cannot be duplicated."

## Risks
- A new write path forgets an idempotency guard → checklist in review; dedup constraints where a duplicate would be financial.
- Key design mistakes (too broad/narrow) → per-surface documented keys.

## Revisit when
A new external integration or write path is added — design its idempotency key + guard as part of the work, not after.
