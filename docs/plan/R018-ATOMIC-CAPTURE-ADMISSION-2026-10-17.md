# R018 — Atomic per-file capture admission

AI-assisted implementation, regression tests and evidence drafting. Continues
R017 from `a922eb6`, whose application CI completed successfully; the image-security
gate remained red. Human/lead review and final-head CI remain required. No main
merge, deployment or release/security waiver is part of this repair.

## Problem and resulting behavior

Capture admission committed three times: `start_capture` persisted the registry
and queued capture; `jobs.enqueue` persisted the extraction job; `record_usage`
persisted the upload count. An enqueue error could strand a capture with no job.
A later failure could expose work to a worker before metering finished, or return
an error after some facts had already committed. Retrying then hit the duplicate
advisory instead of recovering a wholly rolled-back admission.

The per-file admission helper now owns one commit for its document-registry row,
capture, native job intent and metering increment. `start_capture` and
`record_usage` accept `commit=False`, while preserving their previous committing
default for standalone callers. Capture creation flushes its UUID before it is
used in the native job payload/key. The existing job helper already supports
caller-owned transactions. An exception in the admission unit rolls back all
four database facts; earlier accepted batch files remain committed independently.
A 202 is produced only after commit and capture readback.

The database upsert and billing acknowledgement/reporting watermarks are unchanged.
Quota/type/scan/duplicate checks retain their existing order and behavior. Plan-policy
bootstrap, if needed, can still commit during the initial quota check before the
business unit starts; the one-commit guarantee concerns the four admission facts.
R017's definite storage refusal and ordered per-file outcomes remain intact.

Object storage remains outside the database transaction. A failed admission can
leave an unreferenced content-addressed object, but no committed registry/capture/
job/usage fact for that failed file. Do not delete that object in exception cleanup:
other existing records may share those bytes. Orphan inventory and retention remain an operator/storage-lifecycle concern;
this repair adds no automatic orphan scanner or deletion policy. No new broker, table, migration, dependency
or frontend change is introduced.

## Regression and execution evidence

Before repair, the queue/meter failure tests retained committed facts, a failed
second batch file left two admissions, and the explicit noncommitting-service
contract did not exist. Four SQLite tests failed, then passed. They now prove
rollback after actual native queue/meter writes, successful retry without a phantom
duplicate, preservation of an earlier accepted batch file, and caller-owned service
rollback. Existing standalone helper behavior remains covered by related tests.

Two real PostgreSQL cases also failed before repair. While admission was paused
before metering, an independent connection observed `[1, 1, 1, 0]` registry/capture/
job/counter rows and could claim the native job. After repair, it observes
`[0, 0, 0, 0]` and cannot claim any job. Releasing the barrier either commits all
four facts or injects a post-metering exception and rolls all four back. A retry
after rollback creates exactly one complete admission; the real native worker
then parses it, with the counter still one. Both cases verify the application
role is neither superuser nor BYPASSRLS and are registered in the required CI list.

- **58 required PostgreSQL checks passed** on an isolated, freshly migrated PG17
  database using a non-superuser/NOBYPASSRLS application role. This runs the actual
  CI test-file list, including both new cases and existing RLS, queue/savepoint,
  usage-concurrency and financial guards. CI uses its existing PG16 matrix.
- **84 related backend tests passed, one PG-only case skipped** in that local
  SQLite selection; the separate PostgreSQL group covers the skipped case.
- Full local backend verification (`pytest -q -n 4`, Python 3.12.14): **3,671 passed, 41 skipped**, 622.60 seconds. The skipped PG-only cases are covered by the separate required PostgreSQL job selection.
- Backend lint/format and mypy **418 source files** passed. Nine documentation and
  PG-registration checks passed; workflow action pins and current-tree PII passed.
- An unmocked live API → local store → native worker → download check in a fresh
  synthetic workspace returned 202, reached parsed, and preserved **165 bytes**,
  SHA-256 `06e2418b58e09c2a5f48579e8012ed90dedffe73cc744c3417f9bb9518603077`.
  Registry/capture/job identity was linked and usage was exactly one. No customer
  data or external processor/delivery was used.

The collector reports **3,712 tests**; README updated. Frontend sources are unchanged;
previous browser/build/bundle evidence remains tied to its original source, with
fresh full CI required for this head. No test, severity threshold, upload cap or
bundle budget was weakened.

## Limits and rollout

This is atomicity of admission facts, not durable client command identity. A lost
response or commit-connection error can leave the client uncertain whether the
whole unit committed. It must review captures/duplicate evidence before an
intentional override; this repair adds no automatic resend or definitive failure
claim after an uncertain commit. Unexpected batch errors still require checking
which earlier files were accepted before retrying.

The existing quota check and counter increment are not made serializable across
concurrent requests by this change. Per-file locking/reservation would be a
separate admission-policy/concurrency repair; no stronger hard-cap guarantee is
claimed here. Storage fsync/power-loss, S3 delivery and production backup recovery
are also outside this verification.

Deploy the API code together with its compatible helper definitions; normal native
workers keep the same job kind/payload. No schema rollout/backfill is required.
Rollback restores the split-commit hazard, so prefer a forward repair. Do not
rewrite existing captures, job history or usage to invent missing admission facts;
pre-fix stranded/ambiguous records require evidence review rather than blind jobs
or charges being inserted.

Action / engineering prompt: Upload must persist registry, capture, native extraction
intent and upload usage as one per-file database unit. Preserve earlier accepted
batch files, capture UUID/job identity, ordinary quota/security/duplicate checks and
billing watermarks. Prove failure after real queue/meter writes rolls back, a second
PG connection cannot claim precommit work, postcommit processing succeeds, and retry
after a definite rollback produces one coherent admission. Keep transport uncertainty,
shared-object retention and concurrent-quota limits explicit.
