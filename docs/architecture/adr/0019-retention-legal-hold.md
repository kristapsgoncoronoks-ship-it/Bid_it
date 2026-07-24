# ADR-0019 — Data retention + legal hold

**Status:** Accepted — implemented (Phase 4).

## Context
Enterprise/GDPR buyers require **storage limitation** (GDPR Art. 5(1)(e)): personal and business data must not be kept longer than necessary. The counter-duty is **legal preservation** — when litigation or an audit is anticipated, data under scope must be *preserved* even past its retention window (an e-discovery "legal hold"). We need both, per-tenant, admin-controlled, and provable.

## Selected approach
A **retention policy** per (tenant, data category) = "keep records N days since creation, then purge." Categories are a small registry (`invoices`, `expenses`, `email_intake`). The **absence** of a policy = keep forever, so retention is **opt-in and safe by default**. A daily queue job (`retention.purge`, enqueued only for tenants with a policy) and an on-demand admin endpoint run the purge; both:
- **Honour an active legal hold** — any active `LegalHold` row suspends *all* purging for the tenant (preservation overrides minimization).
- **Delete explicitly** — children first, then parents — so behaviour is identical on SQLite and Postgres regardless of FK-cascade enforcement, and **object-storage bytes** (receipts, email attachments) are removed with the rows.
- Are **audited** — `retention.policy_set`, `retention.hold_placed/released`, and `retention.purge` (with per-category counts) land in the hash-chained audit trail.

**Measured from `created_at`** (ingestion time) — a uniform, predictable basis across categories, rather than per-model business dates. The trade-off (a document dated long before it was ingested ages from ingestion) is acceptable and documented; ledger windows should be set conservatively.

## Deliberately NOT purged
- **`audit_events`** — the tamper-evident compliance record is never purged by retention; deleting it would break the hash chain and destroy the evidence retention exists to produce.
- **`issued_invoices`** — gap-free numbering + the audit snapshot make deleting *issued* sales invoices a separate, carefully-gated decision (statutory accounting-retention periods, typically 7–10 years). Excluded from the registry for now.

## Alternatives considered
- **TTL / DB-native partitioning drop** — efficient at scale, but can't express a legal hold, doesn't clean object storage, and isn't per-tenant-configurable. Revisit at very large scale for the high-volume categories.
- **Per-record holds / retention** — finer-grained (hold a single matter), but materially more complex UX + data model. Org-wide hold is the standard first primitive; per-record is a later refinement.
- **Hard-delete vs crypto-shred** — for object storage at scale, deleting the per-tenant/per-object key (crypto-shred) can be cheaper than byte deletion; today we byte-delete via the storage abstraction. Revisit with KMS-per-tenant.

## Risks
- **Irreversible deletion** → opt-in (no default policy), a legal hold that blocks purging, a dry-run **preview** count in the UI before anything runs, and full audit. 
- **Large purge batches** (`IN (ids)`) → fine at current scale; batch/limit is a follow-up for high-volume tenants.
- **Purge ↔ hold race** (hold placed mid-purge) → purges are short, per-tenant, and re-checked each run; worst case one more daily purge before the hold is seen. Acceptable; a per-run hold re-check at the top bounds it.

## Revisit when
A tenant needs per-matter (per-record) holds or category-specific business-date retention; or purge volume needs batching / partition-drop; or statutory ledger retention is modelled (then `issued_invoices`/`invoices` get first-class, longer-default policies).
