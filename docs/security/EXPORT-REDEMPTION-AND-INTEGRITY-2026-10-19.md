# Transactional export redemption and artifact integrity

**19 October 2026 · CAP-EXPORT-REDEEM / CAP-EXPORT-ARTIFACT-INTEGRITY · implemented and locally qualified; new-head CI and Lead review pending.**
AI-assisted investigation, implementation, tests and documentation. Separate agents
reproduced the failures, reviewed production changes, and qualified PostgreSQL and
the shipped interpreter. No external implementation code was copied.
[Sanitized evidence](evidence/EXPORT-REDEMPTION-AND-INTEGRITY-2026-10-19.json).

## Confirmed failures

On predecessor `0d81c0f7d244c911d739a25db2ef49a6ca9f50c2`, two independent
HTTP requests redeeming the same workspace or archive token both return **200**,
each preparing the ZIP and recording a download audit. The controlled PostgreSQL
baseline fails both intended one-use assertions. A prior ten-scenario reproduction
also verified healthy audit chaining: a valid chain does not prevent duplicate
business actions.

The shared token helper reads unused tokens without a lock, then marks its ORM
object used. The original auth-token test baseline has **four failures / ten
passes**: both email-verification and password-reset purposes allow duplicate
claims after commit and accept a token that expires while a competing transaction
is waiting. An expanded isolated old-method control has **six failures / twelve
passes**, also reproducing stale cached expiry. That control loads the exact
committed method into an isolated pytest process; checkout production code is not
reverted.

Finished export downloads also trust bytes under their content-addressed key.
Changed bytes can return **200**, consume the token and mark the export downloaded;
restoring the original then leaves an invalid link. The original artifact test
baseline has **13 failures / eight passes**, with every failure reaching the
intended 503 assertion after an actual 200. It covers same-length replacement,
expansion, truncation, empty replacement and incorrect recorded size. Two later
negative-size controls also fail against the captured original handlers. These
negative controls are credited separately, not as a single original test run.

## Repair and transaction contract

`verification.consume` now selects the unused, purpose-bound token using
`FOR UPDATE` and refreshes existing ORM state. PostgreSQL holds that claim until
the caller commits or rolls back. A waiter re-evaluates the unused predicate after
a winning commit; rollback permits another claimant. Expiry is checked after the
lock wait, using the freshly loaded expiry. The existing tenant SELECT guard and
public token-as-credential scope are retained. No service commits unrelated work.

Both export downloads also lock and refresh their selected ready export. Workspace
exports keep their exact token binding and purge guard. Archive exports retain the
existing newest-ready-for-user policy, now with **LIMIT 1**, so selecting one does
not claim every eligible archive row. Different tokens cannot download the same
single ready artifact twice. This does not create exact archive token binding.

A shared export-artifact helper reads using the recorded size as its storage
budget and verifies exact size and SHA-256 before marking downloaded or auditing.
Known negative sizes fail before storage I/O. Recorded zero uses the storage API's
positive minimum read budget, then still requires exact zero length and hash;
there is no new ZIP parsing/admission policy. SHA hashing runs off the event loop.
Historical nullable sizes retain unbounded reading, but their hash is still
verified. Current upload limits are not applied to previously admitted exports.

Expansion, changed hash and inconsistent size return static
**503 / export_integrity_unverified**, with **private, no-store**, no attachment
header, and no internal path/key in the response. The text directs restoration of
the original and retry. Existing unavailable-read responses retain
**503 / storage_read_unavailable**. Genuine missing objects and invalid, expired,
used or wrong-purpose credentials retain their opaque 404 contract and existing
headers. No success is emitted before commit.

Failure during reading, verification or the final pre-commit operation rolls back
token use, export status and audit. Restoring exact original bytes allows the same
link to complete once. The controlled PG test also proves that an already-waiting
request can complete after the first transaction rolls back. This is not a promise
that a browser receives every byte after a successful commit.

## Qualification

- **23 new artifact tests pass**, plus **265 existing storage/export recovery checks**.
- **30 new PostgreSQL cases** (12 export HTTP / 18 shared auth tokens) pass in
  the **170-case required CI selection**, followed by **six growth checks**.
  The final selection takes 78.83s; growth 11.97s. The additional multi-archive
  case proves newest selection, no lock on the older artifact, and later download
  under the existing policy. Removing only LIMIT 1 in an isolated process makes
  that regression fail with PostgreSQL row-lock refusal, as intended.
- **121 compatibility/security/architecture/doc/supply-chain controls pass**;
  Ruff/format cover **843 files**, Mypy **422 sources**, all without errors.
  Collection is **4,423 cases**, not 4,423 executed passes.
- Rebuilt backend SBOM/HIGH/CRITICAL audit: **zero advisory rows**. All six
  native S3 groups pass with cleanup true and matching tested/scanned backend ID
  `sha256:66d910b188a23281d2813f794c3edadabb2ee0edeb10b0528de3072b20ada2cc`.
- **108 shipped-image cases pass**, zero skips, in three fresh containers:
  77 S3 / eight local recovery / 23 integrity. All 422 application-source and
  422 fixture hashes match; no app source is mounted. Cgroup peaks are
  270.65 / 217.44 / 311.11 MiB, each under the unchanged 384 MiB ceiling,
  with zero OOM events and Docker OOMKilled=false. All owned resources are removed.
- The required PostgreSQL workflow explicitly names both new PG files. Its 30 new
  cases are conditionally skipped in SQLite and run in the required PG job.
  Selection enforcement remains enabled; no gate, assertion, timeout,
  vulnerability severity or resource budget is relaxed.

The initial combined 108-case native process exits137 after 95 passing progress
dots, before final metrics, with no assertion traceback. Auto-removal prevents
inspection of Docker OOMKilled; the cause is unknown. That failed attempt is
preserved and is not credited as 95 passes or hardware acceptance. Fresh unchanged
containers qualify the same cases within the same limits. Inherited Docker HTTP
health probes fail because this command is pytest and starts no API server;
service health is not assessed by this test fixture. Capacity remains open.

An initial extra LIMIT-control invocation used a nonexistent relative interpreter
path, exited127 and ran no tests. The corrected absolute-path invocation above
establishes the meaningful negative control. A local growth-controller argument
assumption stopped before executing that stage; the exact workflow command was
then run successfully, and the final 170+six controller runs both stages without
changing source. These invocation mistakes are not credited as application defects
or gate evidence.

The PostgreSQL fixture uses migrated PostgreSQL 16.15 and a
NOSUPERUSER/NOBYPASSRLS application role, with FORCE RLS on token/export tables.
Independent request connections and an observer establish actual database lock
contention before releasing the first transaction. Storage and pre-commit failures
are synthetic; export endpoints, ORM transactions and audit verification are real.
File-backed S3 tests use the real adapter and botocore streaming bodies with a
synthetic provider seam. They are not external provider certification.

The shipped-image checks qualify Python 3.14.8 independently from host Python3.12.
Native pytest uses synthetic data, UID10001, a read-only root, no network, one CPU
and 384 MiB. The separate S3 checker uses owned RustFS resources and synthetic
credentials. No application source bind, production provider, customer data or
cutover is involved. Local image identities differ from independently built hosted
CI identities; each is matched only to its own evidence.

## Design review, limits and rollback

Proposal: serialize token and selected-export claims inside the existing caller
transaction, and verify recorded artifact identity before committing download.
The expected benefit is one successful redemption with truthful downloadable bytes
and recovery after refusal, without new infrastructure, schema or dependencies.

An atomic UPDATE/RETURNING was considered. The reviewer objected that the existing
ORM tenant hook scopes SELECT statements, so UPDATE would need explicit tenant
handling; a clock captured before waiting would also need separate expiry logic.
The implementation decision uses locked SELECTs with refreshed state and checks
expiry afterward. Reviewer findings also required archive LIMIT 1, negative-size
refusal before reading, off-thread hashing and private/no-store integrity errors.
A separate reviewer found no blocking defect in production changes and the two
export test files; that reviewer authored the shared-token test file, so its
re-review is not independent authorship. Human Lead review is still required.

This concurrency guarantee is for PostgreSQL at the qualified READ COMMITTED
isolation. Production settings reject SQLite; SQLite ignores FOR UPDATE, so this
work does not establish portable SQLite concurrency protection. No migration,
financial calculation, permission, retention/legal-hold rule or provider selection
changes. Restoring the previous code removes these safeguards; rollback is a
reviewed source revert and rebuild, not a data/schema reversal. It does not undo
already committed downloads or repair historically incomplete artifacts.

Remaining boundaries: historical unknown-size reads and large admitted exports
still buffer; exact archive token-to-artifact binding, purge races, and recovery
after commit but before client receipt need separately reviewed contracts. Existing
ready ZIPs are not rebuilt, relabelled or deleted. Legal holds and production
bootstrap refusal remain unchanged. No merge, deployment or human acceptance is
performed by this task.

## Green predecessor

The exact predecessor passed
[CI 39256160223](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39256160223)
and [supply-chain 39256160225](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39256160225):
**4,248 backend / 122 existing skips**, **140 required PG + six growth**,
**620 browser + 13 visual**, seven required jobs, ten clean image targets and all
20 report hashes independently verified. The six native storage groups passed,
cleanup succeeded and tested backend/server identities matched scanned identities.
These are predecessor results, not acceptance of the new revision. Exact new-head
results will be attached to the draft PR without creating a CI-only evidence commit.
