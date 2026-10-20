# Preserve local integrity errors and primary failures

**19 October 2026 · CAP-DIGEST-ERROR-CLASS · implemented, pending new-head CI and Lead review.**
AI-assisted investigation, implementation, tests and documentation. Independent
agents reproduced adapter faults, reviewed caller contracts and the final source,
and qualified the shipped interpreter. [Sanitized evidence](evidence/CAP-DIGEST-ERROR-2026-10-19.json).

## Failure and repair

`LocalStorage.digest` caught `FileNotFoundError` around lookup, hashing and context
cleanup together. A file opened successfully could therefore be reported as
missing after a read/close failure. A secondary close error could also replace a
primary read/hash failure, including cancellation. This was a classification and
exception-precedence defect; the observed descriptors already closed once.

Before implementation, the new adapter/route selection had **14 failures / 21
passes**. Five additional HTTP cases had **three failures / two passes**: existing
objects were incorrectly reported as missing instead of errors. Tests inject
faults around actual local descriptors; they do not simulate a physical-disk fault.

The adapter now distinguishes lookup from hashing. Only a missing file at lookup
retains the existing missing-object contract. Filesystem failures after opening
become sanitized `StorageReadError`, preserving the primary cause internally.
An acquired source receives exactly one close attempt. A secondary filesystem
`OSError` cannot replace a primary exception or cancellation; a close failure
after successful hashing invalidates the result. Non-I/O programming failures
remain their original exceptions. This is a local-file guarantee, not a guarantee
for arbitrary custom source cleanup or S3 response bodies.

Integrity verification retains HTTP 200 with an unhealthy report and an `error`
finding for failed verification, using static retry guidance without paths, keys
or private diagnostics. It continues checking other references. Restoring storage
allows the same verification request to report healthy without rewriting source
objects. Genuine missing files and mismatched bytes retain their existing findings.

Hashing still requests fixed **64 KiB** chunks without loading the entire object
or imposing today's upload limit on historical files. Ordinary local reads, S3,
authorization, financial calculations, schemas, jobs and dependencies are unchanged.
The existing historical-file observer gains the standard `close()` method; its
finite-read, mismatch, restoration and closed-descriptor assertions are retained.

## Validation

- Focused storage/digest/integrity/export selection: **194 passed / 80 warnings**.
- **40 new cases**, including actual HTTP classification/continuation/restoration,
  missing/open/read/hash/close faults, simultaneous failures, cancellation and
  historical files larger than the current upload limit.
- Full Ruff/format: **837 files**; Mypy: **422 source files**, no issues.
- Backend collection: **4,293 cases**, a collection count rather than executed passes.
- Documentation truth, supply-chain controls and structural PII selection:
  **37 passed**. An empty owner-held denylist does not clear private identifiers
  or historical data.

Ten native fault/recovery checks pass on **Python 3.14.8 / UID 10001**, using the
retained runtime image with a byte-identical read-only bind of the changed module.
The module hash is verified inside the process with fresh bytecode lookup. The
container has a read-only root, no network or credentials, a 32 MiB temporary
filesystem, one CPU and a 384 MiB ceiling; it uses no existing volumes and is
removed afterward. Healthy 1,572,889-byte and empty originals, post-open read/close
faults, true missing lookup, primary exception/cancellation preservation and exact
recovery pass. This qualifies the changed source on the shipped interpreter;
it is **not a locally rebuilt-image result**, peak-memory measurement or physical
storage/provider acceptance. Hosted CI will build and audit the new image.

## CI baseline and review boundary

Before this repair, head **e92e2699900631b426c88d9bf7b79c745ba1b8b6** passed
[CI 39238995087](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39238995087)
and [supply-chain 39238995004](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39238995004).
All seven required CI jobs passed: **4,131 backend / 122 skipped**, **140 required
PostgreSQL + six growth**, **620 browser + 13 visual**, frontend, lint, PII and Docker.
No new skips were added by the preceding storage candidate. Deployment and manual
baseline refresh were skipped as expected for the draft PR.

Artifact **12181236625** independently verifies all **ten image targets clean**,
all 20 SBOM/audit hashes and immutable identities, and all five native S3 groups
with cleanup true. The tested native backend/server identities match the scanned
images. These results clear the preceding CI image failure, not human production
acceptance or this new digest head. Earlier 47-row inventories remain historical.

New-head CI and Lead review remain required. The existing draft PR is not merged
or deployed. Production provider/cutover selection under Decisions §29, prepared
generated contracts, private history/owner denylist and restore acceptance remain
separate human boundaries. Independent source review found no blocking defect in
this local repair; it is not Lead approval.

**CAP-S3-READ-ERROR-CLASS remains open:** provider/read failures from S3 `get`
can still collapse into missing. Separately, simultaneous S3 digest cleanup faults
can mask a primary exception; that source gap needs its own reproduction. Neither
is repaired here. Fifteen uncapped original/download/render/delivery/export reads,
total verification I/O/time, aggregate concurrency, decoding, shared disk, fleet
and minimum-hardware qualification also remain. This change makes no global
capacity or release-readiness claim.

Rollback the local digest helper and split exception handling together. There is
no migration or data mutation. Reverting restores the previous misleading failure
classification; the earlier streaming and ordinary-read repairs remain separate.
