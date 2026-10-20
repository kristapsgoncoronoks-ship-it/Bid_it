# R021 — Capture download access and original-byte truth

AI-assisted diagnosis, implementation, tests and evidence drafting. Continues R020
from `317094c`. PR #64's application, PostgreSQL, browser, dependency and workflow
checks passed; its final-image security check failed. Human/lead review and fresh
exact-head CI remain required. No main merge, deployment or security waiver is part
of this repair.

## Problem and resulting behavior

Original and page-image downloads authenticated before object-storage I/O, then
returned bytes using that cached identity/capture. They could deliver a document
after account deactivation, membership suspension, session revocation or workspace
suspension committed while the read was running. A capture deleted or retargeted
during the read still returned its old bytes. Page previews checked SHA-256, but
original downloads returned corrupt content and inconsistent registry sizes as 200.
Originals also lacked the private/no-store response policy already used by previews.

Both byte endpoints now refresh the pinned caller through R020's canonical,
noncommitting identity helper after storage returns. They re-read the tenant-scoped
capture and verify the requested source/page is still the same visible object.
Changed access gets the normal credential refusal; removed/changed capture or page
gets opaque 404. A preview must match the current manifest's digest and byte size.

An original must match the capture's stored SHA-256 and, when a registry row exists,
its recorded size. A mismatch returns a generic 503 with restoration guidance and
no document bytes. Legacy captures without registry metadata retain the previous
application/octet-stream fallback, but must still pass the authoritative capture
hash. MIME is never guessed. Existing content disposition, nosniff and document CSP
behavior remain; originals now explicitly send `Cache-Control: private, no-store`.

No new table, migration, dependency, public request field, frontend change or worker
payload. Upload admission, quotas, accepted jobs, history and review corrections are
not modified. The two byte endpoints use the same existing INVOICE_READ capability.

## Regression and execution evidence

Twelve real PostgreSQL cases failed against R020, each returning stale bytes as 200:
eight account/membership/session/workspace revocations across source/page reads, and
four deletion/retargeting cases across those endpoints. They mutate committed state
through a separate connection after the real storage read and before returning to
the handler. After repair the revocation cases refuse 401; visibility changes refuse
404. Role is verified non-superuser/NOBYPASSRLS by the shared isolated fixture.

Two SQLite corruption cases also failed before repair: altered bytes of the same
length and mismatched registry size both returned 200. The missing cache-policy case
failed too. All now pass; a positive exact-byte check and the legacy missing-registry
fallback remain supported. These four cases plus the twelve PG cases and existing
N4 render/storage tests passed as a focused group of 25. The new PG file is explicitly
registered in required CI.

- **88 required PostgreSQL checks passed**, 77.91 seconds, migrated PG17 with a
  non-superuser/NOBYPASSRLS application role, using the actual CI test-file list.
  CI retains PG16. Existing admission atomicity, quota and access guards pass
  alongside the twelve new in-flight download checks.
- **75 related backend checks passed**, 128.40 seconds, covering original/page
  rendering and serving, capture admission, intake, retries and ordinary quotas.
- Full backend verification: **3,675 passed, 71 skipped**, 745.59 seconds
  (`pytest -q -n 4`, Python 3.12.14). PG-only cases are covered separately
  by the required group above.
- **Four browser page-preview checks passed**, 14.9 seconds: page navigation,
  missing-page recovery, visible download failure and original-viewer fallback.
  These use mocked HTTP responses for client behavior; backend evidence is the
  separate PG and live API work, not the browser fixture outcomes.
- Backend lint/format passed **807 files** and mypy **418 source files**. Nine
  docs/PG-registration checks, action pins and current-tree PII passed. Collector:
  **3,746 tests**, README updated. Frontend source is unchanged; fresh full CI is
  still required. No test, cap, severity, quota or bundle budget was weakened.
- Unmocked loopback HTTP API → PG/local store → native worker → original/page
  downloads passed on a fresh synthetic PDF/workspace. The worker reached parsed;
  usage stayed one. Original **1,530 bytes**, SHA-256
  `150c733e6f844e0d4bdb03ba96e5fad56288f3a23c0f3bf2a8bf08ef5d3816ec`,
  matched the submitted bytes exactly. The actual renderer's JPEG was **21,229
  bytes**, SHA-256
  `4df04ab8ceb333a52acacbc9e38e392c08aeab941d1375a9a04db3fcbe07972c`,
  matching the persisted manifest. Both responses carried private/no-store.
  These hashes identify that one execution, not deterministic PDF generation.
  No customer data, external scanner/provider or delivery. Dedicated API/PG
  test instances were stopped afterward.

## Actions, recovery and rollout

Download original: return only the still-visible original whose bytes verify against
capture evidence. For 401, regain authorized access/reload the correct workspace.
For 404, reload the capture/manifest; it may have changed or been removed. For an
integrity 503, an authorized operator must inspect and restore the verified original
and its corresponding metadata from trusted evidence/backup. Repeating a download
cannot repair corruption. Do not change a stored digest/size to make altered bytes
pass, delete shared objects, overwrite history or invent a replacement legal source.

Page preview / Retry page: the image must remain in the current manifest and verify
its digest/size after the read. A missing/changed/corrupt preview stays 404, with the
existing original-download fallback. Existing UI download errors and page retry are
used; no new destructive or automatic retry action. The original fallback also
verifies its own bytes before delivery.

Deploy the compatible route code and existing R020 identity helper to every API
replica. No migration/backfill; native workers keep their payloads. Rollback restores
the stale-access/integrity gap, so prefer a forward repair. Keep existing image and
owner-dependent release gates open.

Refresh relies on the existing PostgreSQL READ COMMITTED isolation. This is a
point-in-time check after I/O, not a global serialized revocation protocol; a change
after the final check can still race response delivery. Already-delivered or locally
saved bytes cannot be revoked, and no-store is a response cache instruction, not
remote deletion. There is no new streaming cancellation or global lock on readers.
Object-storage reads retain their existing buffering/timeout behavior; this repair
makes no new ingress, memory, S3, encryption-key recovery or minimum-hardware claim.
Synthetic local evidence does not certify an external deployment or production
provider. Retry-command/worker fencing is a separate lifecycle concern.

Engineering prompt: Capture original/page downloads must pin the original identity,
refresh canonical access after object-storage I/O, re-read visibility/object identity
and refuse stale or removed results before returning document bytes. Verify original
SHA-256 and available registry size, preserve legacy hash-verified MIME fallback and
page manifest checks, and add private/no-store response headers. Prove committed PG
revocation/deletion/retargeting races and same-length corruption, preserve exact
valid originals and UI recovery, and avoid hidden commits, history rewrites, automatic
resends or digest changes that conceal a damaged source.
