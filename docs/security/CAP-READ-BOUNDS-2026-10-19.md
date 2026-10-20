# Streamed integrity verification and bounded preview downloads

**19 October 2026 · CAP-VERIFY-STREAM / CAP-PREVIEW-READ · implemented, pending new-head CI/Lead review.**
AI-assisted investigation, implementation, tests and documentation. Separate agents
reviewed contracts and implementation; human acceptance remains pending. No external
implementation copied. [Sanitized evidence](evidence/CAP-READ-BOUNDS-2026-10-19.json).

## Confirmed failures and repair

The document-integrity sweep previously loaded each complete object before hashing
it. A regression refuses unbounded file reads: the preceding verifier fails on a
file admitted before lowering the upload limit. An isolated negative control loads
the exact prior committed `_check` function; it fails the strengthened current
HTTP regression without editing tracked application files.

`Storage.digest()` now hashes local/S3 sources in fixed **64 KiB** chunks until
EOF. It opens and closes its own source inside the worker thread; no complete
object buffer is assembled. MemoryStorage hashes its existing immutable bytes
without copying. The adapter preserves tenant-prefixed keys. Missing objects,
changed content and read/provider failures remain unhealthy findings; read failures
are classified as `error` and never include private provider diagnostics. The sweep
continues to subsequent objects. Existing `Storage.get()` behavior is unchanged.

Historical originals remain verifiable after the upload limit is lowered. This
bounds hashing buffers, not source size, total I/O, query-result metadata, total
runtime or aggregate workers. Cancellation propagates, but a blocked synchronous
read can continue until it settles; its source then closes. No new timeout or
historical-file policy is invented.

Generated previews already have a **512 KiB** output contract. Their download
endpoint previously read a restored **32 MiB** object in full and returned a
misleading 404. The repaired read stops after **524,289 bytes**, distinguishes
`StorageReadTooLarge` before the general storage catch, and returns **503** with
restoration/original-document guidance. It sends no partial attachment. Successful
downloads retain fresh identity/visibility checks, exact size/SHA verification,
inert JPEG headers and private no-store caching. Existing missing/small-corrupt
preview 404 behavior remains. Original bytes and manifests are not deleted or
rewritten by refusals; exact restoration recovers the download.

## Validation

- Focused storage/integrity/preview/access/performance-shape selection: **113 passed / one optional test skipped / 67 test-key warnings**, exit 0. The skip is not counted as a pass.
- Boundary/file-security/foundation/authorization controls: **40 passed**; docs truth **7 passed**; Ruff/format and Mypy **421 source files** pass.
- Required PostgreSQL gate: **131 passed / zero skips / 203 test-key warnings**; single-head migrations/upgrade and explicit app-role proof pass. **Six read-growth checks** pass; informational eight-flight concurrency has zero errors.
- Existing preview browser suite: **4 passed**, no retries. Frontend application code/dependencies remain unchanged; prior head 8ab3d7c also passed hosted frontend/browser/PG/lint/PII and supply-chain while backend was still running at inspection.
- Rebuilt native API: **421 application files match** the checkout. Fresh SBOM/HIGH/CRITICAL audit: **zero advisory rows**. Full build/service gate remains separate and blocked.
- Backend collection: **4,101 cases collected**, not a claim of 4,101 executed passes; README count refreshed.

The fresh native API image runs with actual PostgreSQL 16.15, an app role with
NOSUPERUSER/NOBYPASSRLS, UID 10001, read-only root and dropped capabilities.
Rate limiting is disabled only in this synthetic fixture. A valid CSV is admitted under the 15 MiB limit, then the same API/database is
restarted with a 1 MiB limit. Verification still succeeds. Extending the stored
original to **1 GiB** yields a mismatch and exact-original restoration restores
healthy verification. A deterministic JPEG is persisted through the existing
capture-page service; replacing it with **32 MiB** returns 503. Its original-source
fallback remains downloadable, and restoring the JPEG returns the exact bytes and
headers. A real queued `integrity.verify_documents` worker job succeeds. One
capture, admission command and extraction job remain; references/manifests are
unchanged, with no invoices or payments created. The extraction job itself is not
executed by this fixture.

The native API has **one CPU / 256 MiB**, with actual Uvicorn process high-water RSS **182.55 MiB**. The cgroup reaches **256 MiB** including file cache; **450 max/reclaim events** are recorded, with **zero OOM/OOM-kill events**. This demonstrates recovery under that hard ceiling, not spare capacity. File-cache pressure and process RSS are reported separately. Worker/fixture helpers use separate one-CPU / 384 MiB containers; their peaks are not measured by this API profile. Owned API/PG/fixture containers, networks, volumes and temporary secrets are removed.

Local/S3 unit checks cover short reads, misleading metadata, missing versus denied
or throttled objects, truncated real botocore streams, source closure on read,
update/close failure and propagated cancellation. Browser API fixtures establish
preview recovery controls; server completion is proven separately above. This
local/native evidence does not qualify a real S3 provider, fleet capacity,
worst-case document decoding or minimum hardware.

The required PostgreSQL gate passes. An extra `alembic check` intentionally
retains nonzero diagnostic output for **already registered SYS-06**: PostgreSQL
column-subset SET NULL constraints differ from model/autogenerate rendering.
Those tenant-preserving constraints were not changed. The helper's overall exit
preserves that diagnostic failure; individual required CI steps exit zero.

Initial fixture/command errors are retained and not counted as successful checks:
one wrong test-file path and one nonexistent optional test selection produced no
tests; the first native helper was unreadable to UID 10001 and the second lacked
its `/app` Python import path. Fixes apply only to the synthetic read-only helper,
with secrets kept private. Initial auxiliary memory sampling targeted Tini PID 1;
the final probe identifies the actual Uvicorn Python process. No production
privileges, budgets, assertions or security severity gates were weakened.

## Review, remaining contracts and rollback

Proposal: use a streaming digest for verification and the existing generated-page
budget for preview reads. Applying today's upload limit to historical verification
or all downloads would reject supported older files and generated archives.
Allocating an entire object before hashing retains the original memory risk. No
new framework, dependency, schema, permission or financial mutation is needed.

Adversarial objections: chunked reads must not return a partial hash on a provider
failure, bypass tenant paths, leak diagnostics or claim immediate cancellation.
The storage/HTTP/job tests, actual corruption/restoration flow and PostgreSQL
access checks address these boundaries. Preview success retains the post-I/O
actor/visibility recheck; the safe failure branch returns no document data.

**Lead decision pending:** digest protocol and error semantics, resource evidence,
preview refusal/recovery and the exact new-head CI result. Roll back the digest
protocol/adapter/verifier together; revert the preview route separately if needed.
There is no data migration, deletion or financial rollback. A rollback reinstates
the corresponding unbounded read.

The original 18-call inventory is preserved as dated evidence. There are now
**17 direct load calls: one explicitly bounded preview read and 16 remaining
uncapped original/download/render/delivery/export calls**. Their size/streaming,
authorization, integrity, refusal and retry contracts remain next in the queue;
128 MiB workspace exports and historical artifacts need their own contracts.
The full build/service image gate, SYS-06 tooling drift, shared-disk/fleet pressure,
provider/restore acceptance and human release review remain open. No merge or
production deployment is performed.
