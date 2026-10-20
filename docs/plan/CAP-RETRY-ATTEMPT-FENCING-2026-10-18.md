# CAP-RETRY — atomic capture attempts

AI-assisted implementation and test drafting; no external implementation copied.
Implemented on the development branch, pending exact-head CI and human/lead review.
This is a capture consistency repair, not release approval. The image, historical
PII, production restore and owner acceptance blockers remain open in the
[findings register](../reviews/FINDINGS-REGISTER-2026-10-17.md).

## Defect and operation grain

The PostgreSQL scan paused an extraction worker after it committed `parsed` but
before `record_fields`. Retry deleted the old fields, then the old worker wrote
17 rows onto the queued result. The next worker produced 34 rows instead of 17.
Retry also created unkeyed jobs, so concurrent clicks scheduled competing work.

The operation grain is **one direct-upload capture generation**, identified by
`(org_id, run_id, attempt_seq)`. Initial/legacy generation is zero. Retrying a
completed/failed result advances the generation; clicks while a keyed job is
queued/running return the same pending work. A queued capture whose job is dead
can start a new generation. This is worker replay protection; durable client
admission commands remain the separate CAP-COMMAND item.

## Implementation and transactional boundaries

- Add non-null `extraction_runs.attempt_seq`, default zero, in migration
  `127bc9ad604e`. Existing records are not inferred to have new identities.
- Lock the capture when resetting, correcting fields or publishing results.
  Refresh cached ORM state under the lock. Retry checks corrections before
  deletion and commits the reset, explicit-discard audit and keyed job together.
- Put the generation in job payload/key. A payload without a generation means
  zero; it never adopts the current retry. An old/duplicate job skips publication
  once that generation is complete, superseded or saved.
- Read source bytes, parse/OCR and render previews outside the publication lock.
  Recheck generation/status/invoice link under the lock before writing anything.
  Draft, preview registry/manifest, field provenance and learning observations
  then commit together. Failed publication rolls back and the persisted job
  retries; the UI cannot receive a reviewable half-result.
- Progress uses its own short transaction and the same generation/status fence.
  Closing the parser sink prevents its surviving thread from using a completed
  handler's session. Late progress cannot alter a newer result.
- Revalidate the pinned identity after a retry/review lock wait. A revoked caller
  cannot mutate the result. Invoice linking requires a still-parsed, unlinked
  capture; stale queued/failed/saved saves refuse without committing an invoice.
- Original/page bytes retain the existing immutable-source, tenant and access
  checks. Retries retain the manifest for that same immutable source; they do
  not delete shared objects or introduce a retention policy.

## Evidence

Three PostgreSQL regressions failed against `aae4034` before implementation:
partial publication (success and injected persistence failure), and simultaneous
retry jobs. The original scan's 17→34 result remains historical evidence.

The new PostgreSQL suite uses separate sessions, native persisted jobs and an
ordinary role with neither superuser nor BYPASSRLS. It covers publication/reset
interleaving; stale successful and failing workers with queued, reviewed, saved
and failed successors; review/retry serialization; explicit-discard enqueue
rollback; legacy payload refusal; dead-job recovery; stale queued save/review;
and membership revocation after lock acquisition. Only timing barriers and
specific faults are injected. The parser and job framework run normally.

All **108 required PostgreSQL checks pass**, including **20 new attempt cases**.
The timing-sensitive review/publication cases wait for an observed PostgreSQL
lock wait rather than assuming a request reached the lock after a fixed delay.
Sixty related SQLite capture tests pass; a further 67-case recovery/pages/email
run (including the 20 PostgreSQL attempt cases) passes after fixing a reused
worker session retaining an older generation in its identity map. Handler entry
now explicitly refreshes that row before checking its generation. The rebuilt non-root Alpine image passed
real HTTP PDF upload, separate native workers, simultaneous retries, correction
refusal/explicit discard, invoice save and byte-identical source/page downloads:
three generations/jobs and exactly 17 provenance rows. Its HIGH/CRITICAL audit is
clean; all seven changed runtime/migration source hashes match the tested image.

The broader pre-final backend sweep recorded **3,749 passes, 89 skips and one
failure** (`test_a_retry_that_succeeds_clears_the_failure`), with 3,220 fixture
warnings. That failure revealed the cached-generation issue described above;
it passes in the subsequent 67-case recovery run, alongside the new pinned-row
PostgreSQL regression. The full suite was not rerun after this correction;
exact-head hosted CI must verify the final source. Current collection is 3,841
tests. Ruff/format, mypy (418 source files), generated ERD, nine docs/PG-coverage
checks, action pins and the 15-control generated view pass. The structural tree
PII scan is clean with an empty owner denylist; historical PII is not cleared.
All owned test containers, network, volume and PostgreSQL instance were removed.

PostgreSQL upgrade and the new revision's empty downgrade/upgrade pass. A populated
positive generation correctly refuses downgrade. Full historical downgrade to
base exposed **SYS-08**, an older `sa.DATETIME()` portability failure, also
reproduced from the pre-repair head; it is logged for separate repair. This
limits historical rollback claims, not the successful additive upgrade.

Validation results and the immutable runtime image identity are recorded in the
[repair evidence](../security/evidence/CAP-RETRY-2026-10-18.json). Hosted CI remains
required for the delivered head; local synthetic checks do not establish
production key restore, modest-hardware capacity or accountant acceptance.

## Review: alternatives, risk and adversarial objection

**Proposal/benefit:** one additive counter and existing database row locks prevent
stale publication and duplicate evidence without adding a queue/framework.

**Alternatives:** job-claim locking alone cannot identify a retry generation;
unkeyed jobs plus status checks permit old jobs to adopt a later queued result;
holding a lock through OCR serializes retries behind native CPU work and open
transactions; a new attempt-history table adds lifecycle/migration complexity
without being required to protect the current projection. Audit remains
append-only; this repair does not invent a historical-attempt evidence product.

**Adversarial objection:** a duplicate/reclaimed job can still spend CPU before
losing publication; generation fencing is not an execution mutex or a global
resource ceiling. CAP-RESOURCE retains concurrency/resource qualification. The
short publication transaction includes bounded page-object writes and field
observations; storage or database failure can delay it and retries the whole
publication. An object write followed by rollback can leave an orphan under the
existing CAP-STORAGE limitation; never delete a shared blob as rollback cleanup.

A stale browser editing an older completed generation has no submitted generation
precondition in this API. Locking prevents review/reset overlap and refuses
queued saves, but does not prove that a client saw the latest completed result.
A versioned client review/save contract is a follow-up, not a claim of this repair.
The generic queue's lease completion/reclaim semantics are also unchanged.

**Lead decision:** pending review of transaction/lock ordering, generation-zero
compatibility, resource/latency evidence and rollout. AI-authored evidence is not
self-approval under the [engineering policy](../architecture/AI-ENGINEERING-POLICY.md).

## Rollout and rollback

Quiesce capture API writers and extract workers, back up database and document
storage, apply the additive migration with the application role, then start only
the repaired API/workers. Old workers do not enforce the fence; mixed-version
workers on a live queue are unsafe. Check queued, failed and reviewed synthetic
captures, poll results and verify field counts before resuming ordinary traffic.

Generation-zero legacy queued jobs remain supported; already-parsed/saved rows
are skipped. Historical unkeyed jobs cannot be assigned an invented past
identity. After upgrade, retries use explicit keyed generations.

For rollback, stop writers/workers and reconcile queued work first. Retain the
additive column and generation evidence; do not resume old workers on live queues.
The schema downgrade refuses when any positive generation exists. Restore a
reviewed consistent backup only under the normal database/document restore plan.

## Next prompt

> Implement CAP-COMMAND: durable per-file admission command identity and
> authenticated status lookup after uncertain responses. Bind payload/workspace,
> quota, registry, capture, keyed native job and usage atomically. Prove lost
> response replay, changed-payload refusal, partial batch recovery and revocation
> with separate PostgreSQL connections. Preserve this generation fence and all
> release gates; do not replace duplicate warnings with a false replay claim.
