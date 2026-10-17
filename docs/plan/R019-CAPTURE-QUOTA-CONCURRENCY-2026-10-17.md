# R019 — Concurrent capture quota admission

AI-assisted implementation, regression tests and evidence drafting. Continues R018
from `9e9dc9a`. That head's application, PostgreSQL, browser, dependency and workflow
checks passed; its final-image security gate failed. Human/lead review and fresh
exact-head CI remain required. No main merge, deployment or release waiver.

## Problem and resulting behavior

Two concurrent uploads could both pass the early quota check, then each commit a
complete capture and increment usage. With one available slot, two documents were
accepted. The counter's atomic upsert prevented lost increments but did not enforce
the cap. Scanning also left time for a workspace plan or administrator policy change;
admission continued using the earlier plan/ORM policy values.

Single-file and batch capture now share a final PostgreSQL workspace-row lock after
the bounded read, security scan and early duplicate advisory. Under that lock,
admission selects the current plan and policy as scalar values and checks committed
usage again. It rechecks the duplicate advisory, then stores the original and commits
registry, capture, native job and usage together using R018's transaction. Waiting
uploads see either the preceding commit or its rollback before testing the quota.
A final quota refusal rolls back the transaction and retains the existing single-file
402 or ordered batch `upload_quota_reached` outcome. `override=true` bypasses only
the duplicate advisory. Zero still means unlimited; metering/provider watermarks and
earlier accepted batch files retain their existing behavior.

The early check remains a cheap refusal before reading/scanning. It can bootstrap
a plan policy before the admission unit. The final check never invokes that helper's
implicit commit: a missing policy uses the same bundled plan defaults without seeding
or releasing the lock. No global application mutex, new table, migration, dependency
or frontend change is introduced. Other workspaces use different row locks.

## Regression and execution evidence

Three real PostgreSQL regressions failed against R018: simultaneous single/batch
requests accepted two documents at a one-document limit; a plan change during scanning
was ignored; a policy change during scanning was ignored. After repair these pass.
The scan barrier ensures both competing requests passed the early check, rather than
letting a sequential request exercise only the existing refusal path.

Five added PostgreSQL cases cover the race, rollback of the first metering attempt
followed by another successful admission, plan and policy changes during scanning,
and a missing policy while admission is open. Independent connections prove the lock
survives without an implicit commit, uncommitted usage remains invisible, and a second
workspace can acquire its own admission lock. Accepted captures run through the native
worker with exactly one registry/capture/job/counter and usage of one. These cases
extend the already-required `test_capture_admission_pg.py`; no CI discovery gap.

- **63 required PostgreSQL checks passed** in 48.68 seconds on migrated PG17,
  using an application role verified as neither superuser nor BYPASSRLS. This
  executes the actual CI file list; CI retains its PG16 job. Seven capture
  admission cases include R018's two precommit worker-visibility checks.
- **109 related backend tests passed, one PG-only case skipped**, 106.52 seconds.
  The separate PostgreSQL group covers that skipped concurrency case. Existing
  bounded reads, storage refusals, batch outcomes, ordinary quotas and provider
  metering watermarks remain covered.
- Full backend verification: **3,671 passed, 46 skipped**, 603.47 seconds
  (`pytest -q -n 4`, Python 3.12.14). PostgreSQL cases are covered separately
  by the required group above.
- Backend lint/format passed across **804 files**; mypy passed **418 source
  files**. Nine documentation/PG-registration checks, workflow action pins and
  current-tree PII passed. The collector contains **3,717 tests**; README updated.
- An unmocked loopback HTTP API using PostgreSQL accepted exactly one of two
  concurrent upload attempts at a one-slot limit. The batch request reported its
  quota refusal as an ordered outcome (both HTTP responses were 202). An actual
  native worker handler parsed the accepted capture. Registry/capture/job/counter
  were each one, metered usage remained one, and the downloaded **83-byte**
  original matched exactly: SHA-256
  `7309875092234678d0b7131bf6aa3aafd08408119db76de384bd696afd467d8d`.
  Only a new synthetic workspace/source was used; no customer data, SMTP or
  external extraction service. The dedicated PG/API test instances were stopped;
  the normal local API was restarted on the new source and readiness passed.

Frontend sources are unchanged. Earlier browser/build/bundle evidence remains tied
to its original source; fresh final-head CI is still required. No test, quota,
severity threshold, upload cap or bundle budget was weakened.

## Limits and rollout

The concurrency guarantee applies to the shared single/batch capture admission path
on PostgreSQL with the existing READ COMMITTED isolation. SQLite ignores FOR UPDATE;
it remains useful for ordinary development and atomicity tests but does not prove
concurrent hard-cap enforcement. Generic `record_usage` is still an atomic meter,
not a quota reservation API; any future capture writer must use the same lock and
transaction. Invoice-count quota and other workflows are outside this repair.

Reading and scanning happen before locking. Object storage remains inside the final
locked phase so a definitive final quota refusal stores no new original. A slow storage
backend can therefore delay other admissions/settings writes for that workspace;
this is a deliberate correctness tradeoff, not a throughput benchmark or a guarantee
about storage timeouts. The policy used is the committed value observed at the final
check; a later administrative reduction does not erase already-admitted usage.

Object bytes cannot roll back with the database, so a later failure can leave an
unreferenced shared object. Do not delete shared content-addressed objects as error
cleanup or invent retention policy. Transport/commit-response uncertainty still needs
capture review before intentional override; no durable client upload command or
automatic resend is added. Request authentication is unchanged; this slice does not
claim new post-scan membership/session revocation guarantees. No external delivery,
production sizing, S3 durability or security-history clearance is certified.

Deploy compatible API/service code together; existing worker payloads are unchanged.
All capture-serving API replicas must be upgraded for the cross-replica guarantee:
an old writer does not take the new lock. No data rewrite/backfill is needed. Rollback
restores quota oversubscription, so prefer a forward repair and preserve existing
capture/job/usage evidence. Keep final-image and owner-dependent release gates open.

Action / engineering prompt: Upload must recheck the workspace's current plan,
policy and committed usage after scanning, serialize final same-workspace capture
admission through commit/rollback, and preserve ordered single/batch refusals and
atomic registry/capture/job/meter facts. Prove simultaneous last-slot attempts admit
one document, failed admission releases capacity, committed plan/policy changes are
respected, and another workspace progresses independently. Avoid implicit helper
commits, duplicate-override quota bypass, new global locks or deletion of shared
originals. Keep SQLite, mixed-version rollout, storage latency and uncertain-response
limits explicit.
