# Replace unavailable CI storage images with a development candidate

**19 October 2026 · SYS-04 / V05b · draft engineering candidate, not production approval.**
AI-assisted implementation, independent inventory/source review and native
qualification. [Sanitized evidence](evidence/CI-STORAGE-REPLACEMENT-2026-10-19.json).

## Confirmed hosted failure

Head **6185d899cad5940ca6c4fe93b01635909a480605**, [CI 39232327937](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39232327937),
passed backend, PostgreSQL, frontend, browser, lint, PII and supply-chain checks.
Its image job failed only because `minio/minio:latest` and `minio/mc:latest` were
unavailable and unpinned. All nine rebuilt outputs were clean in hosted CI.
Artifact **12176963699** preserves those results; an independent review verified
all 18 SBOM/audit hashes, matching immutable IDs and OS package coverage.
The [earlier repair report](CI-IMAGE-REPAIR-2026-10-19.md) and historical 47-row
inventory remain unchanged evidence.

The full engineering candidate is prepared in the draft PR for testing and
review. Production provider selection remains pending; this does not activate
a production provider or authorize migration.

## Repository repair

The bundled fresh-development store uses official **RustFS 1.0.3**, Apache-2.0,
index `sha256:9ca9559533b537d10482fe923288b676ad08f92a7069d9a17327fbae3cab688b`.
The exact linux/amd64 image is qualified; other architectures are not. Attached
registry provenance identifies the platform digest and source revision, but no
independent signature verification, support SLA or legal approval is claimed.
Upstream supports the current release; this is not a long-term support contract.

The service retains DNS name `minio` and the existing S3 endpoint. Its console is
disabled; only loopback port 9000 is published in development. UID/GID 10001,
read-only root, dropped capabilities, no-new-privileges and bounded tmp/log mounts
are exercised. RustFS receives its actual `RUSTFS_*` credential fields. Matching
AWS credentials reach the SDK initializer, API and worker; production maps the
existing required operator variables consistently, without inherited dev defaults.

New bytes use a separate **rustfsdata** volume. A one-shot guard first inspects
the original **miniodata** mounted read-only. Every entry, including hidden MinIO
metadata, and every inspection error refuses startup. No migration bypass exists.
Never attach old MinIO disk data directly to RustFS, delete it to pass the guard,
or change the Compose project name to make an empty replacement volume appear.

The initializer now uses `python -m app.storage_bootstrap initialize` from the
audited backend image instead of archived mc and a credential-bearing shell
command. It verifies an existing bucket, creates only after authenticated 404,
and rechecks `BucketAlreadyOwnedByYou` races. Denial, foreign ownership, network,
provider, SDK configuration and cleanup failures return static actionable errors
and nonzero exit; no private endpoint/key/provider trace is printed. Acquired
clients close on success, refusal and interruption. Secondary cleanup errors
cannot replace a primary failure. A failed initializer prevents API startup.

The complete audit discovers **ten targets**, ten pinned provenance inputs and
15 image-source occurrences: nine mandatory build/runtime outputs plus one
immutable external RustFS image. Generic scanner code is unchanged. A seeded
regression reintroduces both old MinIO references and proves that both remain
visible and fail the gate; no provider exception or omitted profile clears it.

## Production and legacy-data boundary

Both bootstrap CLI actions **unconditionally refuse ENVIRONMENT=production**
before filesystem/S3 access. The production overlay explicitly propagates that
mode; the base override propagates it too. There is no approval-bypass flag.
Consequently the bundled base-plus-production stack does not start. Deployment
and TLS instructions now state that restriction before startup instructions.
The independent Hostinger local-storage stack and existing deployments are not
changed by this draft source proposal.

An empty mounted legacy directory does not establish database/source identity:
a renamed project or externally hosted source can leave existing references
pointing elsewhere. Use this candidate only with a fresh development database.
It is not a production migration safeguard or a verified historical restore.
Document registry rows alone do not enumerate every historical inline reference.

[Decision §29](../DECISIONS-NEEDED.md#29-residual-nativebuild-image-findings-v05b-2026-10-16)
remains open for production provider/support/licensing selection and cutover.
Any subsequent implementation needs frozen DB/source/target identities, backups,
all references, required versions/metadata/locking/encryption, streamed copy and
hash/size reconciliation, a restore against the frozen DB and reviewed explicit
cutover configuration. Original data must remain recoverable. ADR-0008 residency,
versioning/lifecycle and encryption requirements are not waived or fabricated.

## Qualification

The complete local audit exits **0**: all **ten targets** have zero HIGH/CRITICAL
rows. **157 controls** pass, including **42 bootstrap cases**. Backend collection
is **4,253**, not a full-suite pass claim; Ruff/format 835 files and Mypy 422
application sources pass. The built image matches all 422 application files and
retains the exact 64 distribution versions.

**Ten native checks** run the actual shipped Python 3.14.8 SDK/module, covering
binary/empty objects, bounds/digest/deletion, six unauthorized operations, replay,
restart and independent-volume restore. **Seven actual Compose checks** prove
healthy API/worker startup, production-overlay refusals, real worker storage and
heartbeat, restart and hidden legacy metadata blocking server startup while
preserving its marker. Every owned fixture is cleaned up.

The persisted `scripts/check_s3_storage.py` passes all **five grouped native
checks**, exits 0 and verifies cleanup. Two injected network/volume-creation
timeouts also trigger cleanup with private 0600 secret files removed. CI now runs
this checker after successful image auditing and retains its sanitized JSON in
the security artifact. A failed native command keeps the job red. Exact hashes
and outcomes are in the linked evidence; new-head hosted CI remains pending.
Local disk-exhaustion attempts and an initial test-helper import error are
preserved as uncredited attempts. A stale-image source-hash check also correctly
refused the first artifact after a final cleanup edit; the rebuilt artifact is
verified before use. Cleanup removes only uniquely owned synthetic resources,
obsolete task images and unused build cache. Existing data volumes are untouched.

The whole application backlog, host CI's original TypeScript binary exposure,
SYS-06 reflection drift and human release acceptance remain separate. A clean
image gate does not approve production deployment or close those findings.

## Review and rollback

Keep the PR draft pending exact-head CI and human review. Revert the bootstrap,
Compose, inventory expectations and deployment guidance together. Retain both
volume histories and restore the previously reviewed source configuration; the
archived MinIO images may still be unavailable. No object copy, deletion,
database migration, retention change or production deployment is part of this
repair.
