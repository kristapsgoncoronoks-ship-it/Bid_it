# ADR-0008 — S3-compatible object storage for documents

**Status:** Accepted (implemented for new writes; legacy in-DB blobs read via dual-read until a contract migration drops the `*_data` columns)

## Context
Original invoice PDFs, receipts, and logos are large binary blobs. Some currently live in Postgres (`LargeBinary`) or local disk. Blob-in-DB bloats the primary, slows backups, and doesn't scale; local disk isn't durable or multi-replica-safe.

## Selected approach
Store document bytes in **S3-compatible object storage** (EU region, versioned, lifecycle rules, encryption at rest). Postgres keeps only **metadata + SHA-256 hash + storage key**. Access via a thin `core/storage.py` abstraction (put/get/delete/presign) so the provider is swappable. Serve documents **inert** (attachment + `nosniff`) via short-lived presigned URLs or a streaming proxy under strict CSP.

## Alternatives considered
- **Keep blobs in Postgres** — simple, transactional, but bloats the DB, slows PITR, caps at DB size; wrong for large binaries at scale.
- **Local/NFS filesystem** — not durable, not replica-safe, no versioning.
- **A DAM/third-party doc service** — over-scoped for storing bytes.

## Why appropriate
Object storage is the right tool for immutable binary originals: cheap, durable, versioned, lifecycle-managed, offloads the DB, and supports the integrity/retention story (versioning + soft-delete within the retention window). The abstraction keeps us provider-portable for residency.

## Risks
- Consistency between DB metadata and object (orphans/dangling) → write object first, then metadata; a reconcile/verify sweep; SHA-256 integrity checks.
- Presigned-URL leakage → short TTLs, tenant-scoped keys, audit access.

## Revisit when
Migration completes (→ status Accepted). Reconsider the provider only for residency/cost, behind the same `storage` abstraction.
