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

---

## Addendum — the `exports` class has a lifecycle (PROD-009, 2026-09-09)

Every other document class here holds an original the product keeps for as long
as the record that points at it. `exports` does not: it holds a DERIVED
artefact — a zip assembled for one download — whose only credential is a
one-time link valid for seven days.

Nothing enforced that. From WO-AI until PROD-009 the `exports` prefix appeared
in exactly two places in the codebase, a store and a load, with no delete
anywhere: the link expired and the file stayed, readable by anyone who could
read the bucket, for ever. A monthly export therefore left a monthly copy of
the tenant's data behind it.

The rule for this class, now enforced by `export.purge_expired` (daily, every
tenant, `services/export_artefacts.py`):

* the bytes die with the link — link expiry, or one day after a download so a
  failed transfer is a support answer rather than a rebuild;
* the REQUEST row survives, stamped `purged_at`, because "an export happened"
  is what an audit asks for and it is not the data;
* the `documents` registry row goes WITH the bytes, so the store and the
  registry never disagree — leaving it made the next export read a row it could
  not load and report the tenant's own purged predecessor as a missing file;
* an export never contains the `exports` class, or each one embeds its
  predecessor;
* a legal hold suspends the purge (ADR-0019 addendum).

A derived artefact in object storage needs its expiry written down when the
class is created. This is the general rule this addendum exists to state.

