# ADR-0003 — PostgreSQL as the single primary datastore

**Status:** Accepted

## Context
We need one datastore that handles relational financial data, JSON payloads, full-text search, a durable queue, and strong consistency — while staying cheap to operate.

## Selected approach
**PostgreSQL** as the single system of record: relational tables, `Numeric` money, JSON columns where useful, full-text search, the job queue as a table, and advisory locks for leader election. Managed Postgres in prod with PITR + a read replica. SQLite in dev/CI via the portable `GUID`/`db_tuning` abstractions.

## Alternatives considered
- **Postgres + Redis + Elasticsearch + a broker** — best-of-breed each, but four stateful systems to run, secure, back up, and keep in sync for an early team.
- **A document DB (Mongo)** — poor fit for money/relational integrity and multi-table transactions.
- **Cloud-proprietary DB** — lock-in; portability matters for residency + exit.

## Why appropriate
Postgres does *all* of these well enough to defer specialised tooling until a metric forces it. One thing to operate, one consistency model, one backup story. It scales to billions of rows with partitioning + replicas. The codebase already abstracts SQLite↔Postgres cleanly.

## Risks
- Using one tool for everything hits ceilings (search relevance, queue throughput at extreme scale) → each of those has its own ADR + revisit trigger.
- Blob-in-DB bloat → addressed by ADR-0008 (object storage).

## Revisit when
A measured ceiling appears: search relevance/scale (→ ADR-0014), queue throughput (→ ADR-0007), or write-bandwidth (→ read/write split, sharding). Add the specialised store *for that need only*.
