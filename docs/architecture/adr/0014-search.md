# ADR-0014 — Postgres full-text search before a search engine

**Status:** Proposed

## Context
Users need to find invoices/documents by number, supplier, amount, text content, and metadata. Search must be tenant-scoped and consistent with the record.

## Selected approach
**Postgres full-text search** (`tsvector` + GIN indexes) plus structured filters over the existing tables, tenant-scoped like every other query. Document text extracted at capture is indexed; metadata/tags are filterable. No separate search cluster in v1.

## Alternatives considered
- **Elasticsearch/OpenSearch** — superior relevance + scale, but a stateful cluster to run, secure, back up, and **keep in sync** with the DB (dual-write/CDC complexity + consistency bugs).
- **Typesense/Meilisearch** — lighter than ES, still a second store to sync.
- **`LIKE`-only queries** — no relevance, poor performance at scale.

## Why appropriate
Postgres FTS covers the realistic query set for structured financial documents at our scale with **one datastore, no sync, no extra ops**, and it's transactionally consistent with the record. It defers the real cost of a search engine until relevance/scale demands it.

## Risks
- Relevance ceiling + large-corpus performance → GIN indexing, scoped queries, materialised search columns; measure.
- Cross-field ranking is cruder than a dedicated engine → acceptable for exact-ish financial search.

## Revisit when
Search relevance or corpus size measurably degrades UX, or we need fuzzy/semantic search across large document bodies — introduce a dedicated engine fed by CDC, for search only.
