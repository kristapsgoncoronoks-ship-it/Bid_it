# R020 — Refresh capture access around file work

AI-assisted implementation, regression tests and evidence drafting. Continues R019
from `dfd4eab`; no main merge, deployment or release waiver. Fresh exact-head CI and
human/lead review remain required. Existing image-security and owner-dependent gates
stay open.

## Problem and resulting behavior

Capture kept the principal resolved at request start throughout scanning, storage
and every batch file. Real PostgreSQL tests showed new captures being accepted after
account deactivation, membership suspension, session revocation or workspace suspension
committed during scanning. User/membership/session changes during storage were also
ignored. Switching the same session to another workspace did not stop the original
request. A later batch file could continue after offboarding.

Each file now refreshes the original caller's canonical identity at preflight, after
scanning and acquiring its workspace lock, and after object storage. It checks the
same existing `INVOICE_READ` capability as the route; capture remains available to
all billing tiers under their existing plan quotas. The immutable principal pins
user and workspace for the entire batch. A moved session gets the same opaque 401
credential refusal as invalid/revoked credentials; no lookup adopts the other tenant
or widens RLS access just to classify the change. Workspace status, membership,
active account, token/session validity, residency and expected-identity headers use
the shared identity chain.

The new caller-owned identity refresh flushes pending registry edits, expires cached
ORM values and revalidates without rollback or session `touch`. A best-effort
last-seen update can commit; allowing it inside admission would release R019's lock
and publish partial R018 facts. Ordinary request authentication retains its previous
session-touch default. Capture refresh never invokes it.

The quota service exposes its existing lock and fresh-policy check as separate
steps, retaining `lock_upload_quota` as the compatible combined wrapper. The route
locks the workspace, refreshes access, then reads current quota and duplicate
information. A regression caught a duplicate advisory being returned after scan-time
offboarding; that read now occurs only after the locked access check. Successful
admission still commits registry/capture/native-job/usage once per file. Refusal
inside the admission unit rolls everything for the current file back.

## Regression and execution evidence

Thirteen new PG-only cases are registered in required CI. Seven actual committed
revocation scenarios exercise scan/storage boundaries. Two cases switch the session
through the real switch-org API during those boundaries and prove neither workspace
receives a capture. A later-file offboarding case preserves the first committed batch
admission and does not scan the third file. An aged session tests that refresh neither
touches the session nor makes registry/capture/job/usage visible before final commit.
An explicit reupload preserves updated registry filename metadata and counts both
intentional admissions. The final case refuses scan-time offboarding before exposing
an existing duplicate advisory.

Before repair, the seven revocations and later-file offboarding returned 202. The
switch cases also returned 202 when run against R019 using the actual switch API.
The first refresh implementation still returned a duplicate advisory after scan-time
offboarding; its added regression failed with 409 instead of opaque 401, then passed
when the advisory moved behind the access check. The final targeted group includes
R018/R019 checks for worker visibility, rollback, last-slot competition, current
plans/policies and independent workspace locking.

- **76 required PostgreSQL checks passed**, 59.85 seconds, on migrated PG17
  using a non-superuser/NOBYPASSRLS application role. The actual CI test-file list
  includes the new authorization file; CI retains PG16. The focused capture
  admission group passed **20 cases** before the full PG group.
- **115 related backend checks passed, one PG-only case skipped**, 118.57 seconds;
  the separate PG group covers the skipped concurrency case. Existing single/batch
  quota, resource/storage refusal, metering and session-isolation behavior remains
  covered.
- Full backend verification: **3,671 passed, 59 skipped**, 616.51 seconds
  (`pytest -q -n 4`, Python 3.12.14). PG-only cases are exercised separately
  by the required group above.
- Backend lint/format passed **805 files**; mypy passed **418 source files**.
  Nine docs/PG-registration checks, action pins and current-tree PII passed. The
  collector contains **3,730 tests**; README updated. Frontend source is unchanged;
  prior browser/build evidence remains source-bound and fresh CI is required.
- **12 browser account-isolation checks passed**, 18.9 seconds, including late
  401 quarantine. They use mocked API responses and validate client behavior only;
  the PG and live checks below provide backend evidence.
- Unmocked loopback HTTP API → PG/local store → native worker → source download
  passed with exactly one accepted capture and one quota refusal from competing
  single/batch requests. HTTP statuses were 402 and 202. Registry/capture/job/
  counter were each one, usage stayed one after parsing, and the **84-byte**
  original matched exactly: SHA-256
  `32a2bbabe4ffc2654ae9b42db657cc3d81ebc79218ba01df2c1e43090ae3dfa7`.
  Synthetic source/workspace only; no customer data, external scanner/provider
  or delivery. Dedicated API/PG instances were stopped afterward; normal local
  development API restarted on the new source and readiness passed.

No test, severity, upload cap, quota policy or bundle budget was weakened. The
session-switch response intentionally uses opaque 401 credential semantics on both
backends, matching the RLS visibility boundary rather than introducing cross-tenant
lookups for a 409 classification.

## Action, recovery and rollout

Upload: while the file is scanning, waiting for admission or being stored, a committed
access change observed at the next check prevents a new capture/job/usage admission.
For a definite access refusal, restore authorized access or authenticate/reload the
correct workspace before attempting a new command. Do not blindly replay a failed
batch: earlier files may already be accepted. After regaining access, inspect captures
and duplicate evidence first. The normal 401 client flow requires fresh authentication;
this repair does not invent a per-file authorization-success response.

The batch stops on credential refusal; earlier accepted files stay committed. Ordinary
quota/size/type/duplicate/storage refusals retain their existing per-file outcomes.
No new retry button, public request field, frontend change, schema, dependency or
worker payload is introduced. Deploy compatible API/dependency/quota-service code
together on all replicas. No migration or backfill. Rollback restores the stale-access
window; prefer a forward repair and preserve historical capture/job/usage evidence.

Storage remains outside the database transaction. A post-storage refusal can leave
unreferenced content-addressed bytes; do not delete shared objects in error cleanup.
The workspace lock still covers storage, so slow storage can delay same-workspace
admissions/settings. The lock serializes workspace status/plan writes; the storage
revocation cases deliberately use account/membership/session updates that can commit
independently. SQLite is not evidence for PostgreSQL lock/RLS concurrency.

These committed-state refreshes rely on the existing PostgreSQL READ COMMITTED
isolation, as does R019; repeatable-read snapshots are not covered. They check
defined boundaries, not a global serialized revocation protocol. A change after the final access read can still race the short
remaining database commit. Prior accepted captures/jobs are not automatically
canceled, historical metering/audit is not rewritten, and uncertain commit/response
outcomes still require capture review before intentional override. No production
sizing, S3 durability, external scanner/provider acceptance or release clearance.

Engineering prompt: Capture must pin the original caller/workspace across every
batch file, revalidate canonical access after scanning/lock waits and storage, and
refuse changed credentials before exposing quota/duplicate information or admitting
new work. Preserve the existing permission/plan contract, PostgreSQL quota lock and
atomic registry/capture/job/usage transaction. Never use a session-touch commit or
rollback to refresh inside admission. Prove real PG revocations/switches, no wrong-
workspace writes, preserved prior batch work, no precommit visibility, and retained
registry metadata. Keep storage orphans, point-in-time checks, response uncertainty
and rollout limits explicit.
