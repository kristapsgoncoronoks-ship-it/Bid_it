# SYS-03 — Private, writable fresh storage volumes

AI-assisted diagnosis, image repair, CI checks, synthetic workflow and evidence
drafting. Implemented locally, pending exact-head CI and human/lead review. Follows
SYS-03 in the [full-system scan](FULL-SYSTEM-SCAN-2026-10-17.md). No production
deployment, customer-data migration or security waiver is included.

## Problem and resulting behavior

The image owned `/app/var` but never created the actual named-volume mount target
`/app/var/storage`. A fresh Docker volume therefore appeared as UID/GID 0, mode
`0755`; the runtime's UID 10001 could not store an original document.

The image now creates that target before switching to its application user, gives
it mode `0700` and includes it in the existing build-time application ownership
step. Docker's default empty-volume initialization copies its UID/GID 10001 and
permissions. Both API and worker mount the same directory and use the same UID;
the existing atomic local storage writer creates object files with mode `0600`.
No root runtime, ownership-changing entrypoint or world-writable directory is added.

Existing nonempty volumes retain their bytes, ownership and modes. Incompatible
legacy ownership still refuses writes and needs an operator-reviewed migration;
the repair does not silently change existing documents. Bind mounts, custom paths
and volumes mounted with `volume-nocopy` need separate operator preparation.

## Automated volume contract and exclusion gate

CI now runs [the real-image storage check](../../scripts/check_storage_volume.py)
after building the backend. Its original runtime-data exclusion seed remains.
The former assertion that `/app/var` has no children is replaced with the exact
intended layout: **one empty, privately owned storage directory**, no extra data,
plus an application-user write check of `/app/var`.

The script accepts an image only. It creates UUID-named synthetic volumes and
containers, never accepts an existing customer volume, and removes its own
resources. Eleven stages verify the image layout, original write, page write from
a separate process, cross-process reads, read-only refusal, unrelated-user denial,
pre-existing bytes/modes, container recreation and root-owned legacy refusal with
unchanged metadata. Application checks use UID 10001, dropped capabilities and
no-new-privileges; mounted-volume checks also use a read-only container root.
Fixture-only root with CHOWN prepares an isolated synthetic legacy volume.

## Evidence

[Sanitized machine-readable evidence](evidence/SYS-03-STORAGE-VOLUME-2026-10-17.json)
contains hashes, volume metadata, workflow results and every current advisory row.

- **Original defect reproduced in the actual image:** UID 10001, fresh target
  UID/GID 0 and `0755`, `LocalStorage.put` raises `PermissionError`. The new smoke
  gate also failed against that original image before the Dockerfile repair.
- Full runtime image rebuilt with TLS verification and package signature checks
  preserved. Requirements/base pins are unchanged. Immutable ID:
  `sha256:aedaebcc623309e1d6c959a4b34e6c042d91b3065e97d69ae82b9c1c8fddfaef`.
  A synthetic unwanted file was seeded under local `backend/var` before building;
  `.dockerignore` excluded it and the image gate passed.
- **All 11 native volume stages passed.** New directory UID/GID 10001, `0700`;
  original/page objects `0600`. Pre-existing root mode `0750`, an operator fixture
  at `0400`, hashes and ownership survived a new container unchanged. A nonempty
  UID 0 legacy volume stayed UID 0 and rejected application writes. Read-only
  mount failure left the complete before/after snapshot unchanged. UID 10002 could
  not list the private new store; no cross-tenant authorization claim is inferred.
- **Exclusion counterexample:** an intentionally derived image with an unwanted
  `/app/var/ci-runtime-exclusion-probe` failed the new gate. Permitting the prepared
  empty directory does not permit runtime customer files into the image.
- **Real HTTP/API and separate native worker passed with the same rebuilt image.**
  An isolated PostgreSQL 16 database and fresh named storage volume accepted a
  synthetic PDF (202); the persisted `upload.extract` handler in a second container
  succeeded and the capture became parsed. API downloads matched the **1,456-byte**
  original and **16,251-byte** rendered page/manifest hashes. Original cache control
  remained `private, no-store`.
- A second API with that volume mounted read-only returned **503 and retry guidance**
  on upload, without a private filesystem path in the response. Document/run/job/
  usage-row counts remained **[2, 1, 1, 1]**. Restarting the writable API preserved
  byte-identical original/page downloads and private root/object modes.
- The first restart harness retained an obsolete ephemeral host port and timed out;
  API logs showed successful startup. An independent Docker check confirmed port
  reallocation. The corrected harness refreshes the mapping after restart and the
  full workflow passed. Application code was unchanged for this correction.
- **48 focused storage/deployment/capture checks passed**, 10.93 seconds, with 21
  existing synthetic HMAC-key warnings visible. **26 docs/PG-registration/supply-
  chain checks passed**, 0.27 seconds. Ruff/format checked **809 files**, including
  the new helper; action pins and generated security-control view passed. Backend
  application source, schema, dependency declarations and frontend are unchanged;
  full backend/browser/PG suites were not repeated locally for this image/CI repair.
- Current-tree structural PII, local document links, image/source/evidence identity
  and owned-resource cleanup checks passed. The owner-held denylist is empty;
  current-tree scanning does not clear historical PII or owner decisions.

The native workflow used one API worker and development-mode schema creation with
synthetic data. It proves the shared-volume upload/native-parser/page path and
refusal/restart behavior, not production migrations, RLS, customer restore, OCR
fallback, external storage or minimum hardware. Only owned synthetic resources
were used; no customer data or external processor was accessed.

## Fresh image scan — release still blocked

Trivy 0.76.0 with the existing pinned scanner and updated database scanned this
same immutable final image. The unchanged HIGH/CRITICAL gate exited **1**:
**70 HIGH + 1 CRITICAL rows, 28 distinct advisory IDs**, no listed fixed versions.
All 71 package/version/advisory rows and the scanner database identity are in the
JSON. The critical libxml2 finding remains `CVE-2026-6653`.

The earlier image's 75 HIGH + 1 CRITICAL / 33-ID snapshot remains preserved. OS
packages and the scanner database refreshed during rebuilding; five earlier IDs
were not observed here. This is not a controlled equal-database attribution to
SYS-03, an applicability verdict or a waiver. Builder/service-image gaps and the
historical PII/control decisions remain open.

## Rollout and recovery

After review and security clearance, rebuild/publish the backend image through
the normal release path and use it for both API and worker. Recreate containers;
a configuration-only update cannot add the prepared directory to an old image.
There is no database migration or object-format change. A prepared volume remains
readable by the earlier UID 10001 image on rollback; rollback does not undo bytes.

Before updating or restoring an existing deployment, quiesce intake, verify backups,
inventory root/child ownership and ACLs privately and test both processes on a clone.
Approve a specific ownership or copy/restore plan for incompatible volumes before
changing customer metadata. Do not use recursive `chown` or `0777` as an automatic
repair. An existing nonempty root-owned volume remains an explicit refusal; restore
trusted access before retrying uploads. [Operator contract](../DEPLOYMENT.md#local-storage-volume-initialization-sys-03-pending-review).

Next: V05b/SYS-04 image remediation and complete service-image coverage, then the
confirmed CAP-RETRY race. Exact-head CI, human/lead review and production acceptance
remain required; this item is implemented with evidence, not release-cleared.
