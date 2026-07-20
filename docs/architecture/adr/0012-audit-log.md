# ADR-0012 — Hash-chained append-only audit log

**Status:** Accepted

## Context
Financial records need tamper-evidence and attribution: who changed what, when, and proof nothing was altered — for auditors and for internal-control assurance.

## Selected approach
An **append-only, hash-chained** audit log (`audit_events`). Each event records actor, action, target, UTC-ms timestamp, and a monotonic per-tenant `seq`; its hash chains the previous event's hash. `verify_chain` recomputes a tenant's chain and reports the first break. Recording is **best-effort** (a failure is logged loudly but never breaks the user's operation) and commits **atomically** with the operation it describes. The table is never edited or deleted via application code.

## Alternatives considered
- **Plain audit table (no chaining)** — records history but doesn't prove integrity (a DB admin could edit a row undetected).
- **External immutable ledger / blockchain** — over-engineered; adds a dependency for a property a hash chain + controls already give.
- **App-log-only auditing** — not queryable, not attributed, not tamper-evident.

## Why appropriate
Hash-chaining makes tampering detectable with no external system; best-effort recording means auditing never becomes a source of user-facing failure; atomic commit means the audit and the change are consistent. It directly supports the auditor persona and the "authenticity/integrity" compliance claim.

## Risks
- A gap in coverage (an un-audited change) → audit at the service layer for every state change; review checklist.
- Chain verification cost at volume → verify on demand / sampled; index by `(org_id, seq)`.

## Revisit when
A regulator or enterprise customer requires externally-notarised immutability, or write volume makes per-event chaining costly — add periodic anchoring/segmentation, keep the model.
