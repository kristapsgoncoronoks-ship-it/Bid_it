# Preserve S3 read failures and complete export recovery

**19 October 2026 · CAP-S3-READ-ERROR-CLASS · implemented, pending new-head CI and Lead review.**
AI-assisted implementation, regression tests, investigation and documentation.
Independent agents reviewed storage/caller contracts and the final adapter/native
probe, implemented export regressions and qualified the shipped interpreter.
[Sanitized evidence](evidence/CAP-S3-READ-ERROR-2026-10-19.json).

## Failure and repair

`S3Storage.get` translated every ordinary provider/read/close failure into the same
`StorageError` as an absent object. Permission denial, missing buckets, throttling,
connection refusal and incomplete reads could therefore be treated as missing.
Context cleanup could replace a primary oversize refusal or interruption. S3
digest already distinguished ordinary lookup/read failures, but its cleanup could
also replace the original cause or cancellation.

The pre-repair adapter selection has **34 failures / 21 passes** across 55 cases.
Four actual export regressions demonstrate the consequence: both workspace and
archive workers succeed with ready-but-incomplete ZIPs after denied access; both
finished-ZIP downloads return misleading invalid-link 404s. Two genuine-missing
export controls pass. The corrected source/page HTTP baseline has **four failures
/ four passes**. Provider seams are synthetic; returned bodies use actual owned
file descriptors and botocore `StreamingBody`, including its real length-mismatch
validation. These tests are not external provider certification.

The adapter now separates lookup from reading. Only `NoSuchKey`, `404` and
`NotFound` from `get_object` retain plain `StorageError`, with static missing text.
Other lookup failures, malformed responses and ordinary post-open exceptions
become sanitized `StorageReadError`. Bucket, prefix and key selection are preserved.
Lazy botocore imports retain the optional S3 dependency boundary. Invalid limits
are still refused before provider I/O.

Opened bodies use the existing read/digest cleanup helpers: one close attempt;
primary oversize, read/hash exceptions or cancellation survive a secondary cleanup
`OSError`; successful reading/hashing followed by failed close refuses the result.
Ordinary S3 exceptions are normalized with their primary cause retained; primary
`BaseException` interruptions propagate unchanged. Arbitrary non-I/O cleanup
exceptions are outside this precedence guarantee. No partial bytes or hash return.

Existing callers already distinguish `StorageReadError`. Failed export builds now
remain queued for the ordinary job retry, without ready ZIP, export document,
email or token publication. Restoration/retry builds the complete byte-verified
ZIP. Genuine missing originals retain their explicit missing-document manifests.
Finished-ZIP read failures return static **503 / storage_read_unavailable**, with
private/no-store headers, preserving the existing token, export and download audit
until the same link succeeds once. Reuse then retains the opaque 404 contract.
Existing ready ZIPs are unchanged. Their old missing-document counts cannot prove
whether the source was absent or temporarily unavailable. Requesting a fresh
export after storage recovery provides new evidence; no historical artifact is
automatically rebuilt, deleted or relabelled.

Capture source/page downloads give sanitized retry guidance and recover after
restoration. Unauthenticated requests reach no storage read. Existing integrity,
tenant and fresh post-I/O access checks remain. No caller, authorization, schema,
financial calculation, dependency, upload limit, historical-file size policy,
retention rule or credential/provider selection is changed. Bounded callers retain
cap+1 consumption; callers intentionally using unbounded reads retain that contract.

## Validation

- **260 focused checks passed / 112 warnings**, including all **77 new cases**.
- Full backend Ruff/format: **840 files**; Mypy: **422 source files**, no issues.
- Backend collection: **4,370 cases**, not a claim of 4,370 executed passes.
- Boundary/foundation, documentation, supply-chain/PII, image/bootstrap/Compose and
  workspace/archive compatibility controls: **177 passed / 51 warnings**.
- Rebuilt final backend SBOM/HIGH/CRITICAL audit: **zero advisory rows**, with image
  identity matching the native checker. The full new-head image gate remains CI work.
- All **77 new tests pass / zero skips / 46 fixture-key warnings** on the rebuilt
  Python 3.14.8 image, with no application source bind. All **422 application** and
  **419 fixture/config** hashes match. The isolated pytest/ASGI/SQLite runner uses
  UID10001, a read-only root and fixture volume, no network, one CPU and 384 MiB;
  no OOM events occur. All owned resources are removed. This is not live HTTP/
  PostgreSQL or minimum-hardware acceptance.
- The native checker passes **six groups**, including the new adapter error group,
  on rebuilt image `sha256:a1ad350aab6d9bb7bb3cc7156974fcf2c5e5dcd637b7621afe57afbcc4f9a91f`.
  Python **3.14.8**, UID **10001**, boto3 **1.43.111**, botocore **1.43.114**.

The native checker uses normal adapter construction with actual wrong credentials
against an owned RustFS instance, requiring a 403 cause for both `get` and `digest`.
A refused client-loopback endpoint must yield an `EndpointConnectionError` cause.
Normal authenticated byte/hash reads then recover. Actual missing/deleted objects
must remain plain missing errors. Existing initialization, production refusals,
legacy-data preservation and server-container restart/persistence checks remain.
It runs read-only unprivileged containers, synthetic credentials and new UUID-owned
resources; all cleanup succeeds. This is client transport-refusal qualification,
not provider shutdown, midstream network interruption or production cutover.

The first combined run had **two failures / 258 passes** because a test wrapper
did not accept botocore's `None` argument for unbounded raw reads. The wrapper now
normalizes that argument to the standard file read form. The corrected negative
HTTP control loads only the exact committed pre-repair `S3Storage.get` method into
an isolated pytest process with shared exception types; no checkout source is
reverted. It reproduces all four intended 404/503 failures. The final 260-case
run above uses the repaired production method. Earlier wrapper results are not
credited as truncation or recovery proof.

An initial control selection named nonexistent `test_foundation_guards.py`, exited4
and ran zero tests; it is uncredited. An optional root-script Ruff invocation used
outside-repository settings and reported existing cleanup/shebang rules. Explicit
repository configuration passes both checker lint and format; no rule is waived.
Tracked configuration and CI budgets are unchanged.

## CI baseline and review boundary

Predecessor **b03c4d5cb360e0302281dfa26b5ed307e51179a4** passed
[CI 39247354931](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39247354931)
and [supply-chain 39247354935](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39247354935).
All seven required jobs passed: **4,171 backend / 122 skipped**, **140 required
PostgreSQL + six growth**, **620 browser + 13 visual**, frontend, lint, PII and Docker.
Artifact **12183994556** independently verifies all **ten image targets clean**,
all 20 report hashes/identities and the predecessor's **five** native storage groups
with cleanup true. Its native/scanned identities agree. These results qualify the
local digest predecessor, not this S3 head. New-head CI and human review remain.

The existing PR remains draft. Production provider/cutover selection under
Decisions §29, generated-contract acceptance, history/owner denylist and restore
acceptance remain separate human boundaries. This work activates no production
service and accesses no existing customer volume.

`S3Storage.exists` still maps failures to false, but source tracing finds no caller
inside the running application; it is an unused protocol limitation, not a
confirmed reachable product defect. Invoice delivery retains its existing terminal
attachment-failure behavior before SMTP; an explicit new delivery is the current
recovery. This repair does not invent an automatic delivery retry policy. Inbound
email replay/state findings concern a different workflow.

The **15 uncapped reads**, admitted-payload buffering, aggregate concurrency,
decoding, shared disk, total verification I/O/time, fleet and minimum-hardware
qualification remain. No global capacity or production-readiness claim is made.

Rollback S3 lookup classification and helper adoption together, retaining their
local counterparts. There is no migration or data mutation. Reverting restores
the previous misleading missing/cleanup behavior; preceding streaming/read budgets
remain independent. Earlier dated snapshots are preserved.
