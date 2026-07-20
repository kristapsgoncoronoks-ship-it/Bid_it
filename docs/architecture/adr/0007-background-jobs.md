# ADR-0007 — DB-backed durable job queue over Celery/Redis

**Status:** Accepted

## Context
We need durable, idempotent background processing (recurring invoices, reminders, webhook delivery, extraction) with retries, dead-lettering, and exactly-once *outcomes* — without adding a stateful broker for a small team.

## Selected approach
A **durable queue implemented as a Postgres table** (`jobs`). Atomic claim via a guarded `UPDATE ... WHERE status='queued'`; exponential backoff retries; dead-letter after max attempts; stale-lease reclaim for crashed workers; optional idempotency key unique on `(org_id, kind, key)`. A worker process drains it; handlers run in tenant scope; a date-keyed scheduler enqueues daily periodic work. Enqueue commits **in the same transaction** as the domain change.

## Alternatives considered
- **Celery + Redis/RabbitMQ** — mature, but a broker + result backend to run/secure/back up; at-least-once still needs idempotent handlers; transactional enqueue-with-domain-change is awkward.
- **Arq (Redis)** — lighter than Celery, still a Redis dependency.
- **Cloud queue (SQS)** — managed, but external dependency + residency + loses the transactional enqueue with our DB write.

## Why appropriate
One datastore; **enqueue is transactional with the business change** (no "committed the invoice but lost the webhook"); the queue is backed up with everything else; visibility is a SQL query; it's already built and tested. At our scale, Postgres throughput is ample.

## Risks
- Table contention / throughput ceiling at very high job rates → indexed claim, bounded concurrency, lanes; revisit trigger below.
- Long-polling load → tuned poll interval + `LISTEN/NOTIFY` option later.

## Revisit when
Measured job throughput approaches Postgres limits, or we need cross-service streaming/replay — then introduce a dedicated broker (or `SKIP LOCKED` + partitioning) for the hot job kinds only.
