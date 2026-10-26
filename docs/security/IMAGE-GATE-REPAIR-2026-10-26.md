# Container-image repair — 26 October 2026

**Historical first repair:** the source and receipts below qualify head55's first repair. Subsequent independent review confirmed an additional affected copy inside Node itself. The [Node-core follow-up](NODE-CORE-FOLLOWUP-2026-10-26.md) supersedes any inference of complete remediation from the first clean package scan; its separate receipt preserves this history.

PR #82's original `fc60c823fba711100dc225ba53439e0ac93e082c` passed 4,535 backend cases / 184 skips, 829 functional and 13 visual cases, 202 PostgreSQL cases, six growth checks and nine actual expense journeys. Its image-security gate correctly refused vulnerable packages, so the [original CI run](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/40391301973) remains failed. This isolated repair fast-forwards the same draft PR after local qualification; it does not force-push, merge or deploy.

## Confirmed findings

The digest- and CRC-verified image artifact contains ten HIGH advisory rows, zero CRITICAL rows and three unique advisory IDs across four of ten targets. All twenty exported sanitized SBOM/audit hashes match its summary. Original pre-sanitization hashes cannot be recomputed from the exported files.

| Advisory | Installed package | Fixed package | Affected targets / rows |
| --- | --- | --- | --- |
| CVE-2026-111497 | brotli-libs 1.2.0-r1 | 1.2.1-r0 | Frontend runtime, PgBouncer builder, RustFS / 3 |
| CVE-2026-92392 | curl and libcurl 8.22.0-r0 | 8.23.0-r0 | Same three targets / 6 |
| CVE-2026-109869 / GHSA-xjj6-f33q-929c | npm-bundled undici 6.29.0 | 6.29.1 | Frontend builder / 1 |

The undici finding belongs to `/usr/local/lib/node_modules/npm/node_modules/undici`, not the application lockfile or Node's built-in HTTP implementation. The original tested merge `352cf2091e6a822b2135bcf0fd4a5ad4bcc96584` and PR head have identical whole-repository tree `40a4a988a7ecb4aec918377e0c454232042c54fb`.

## Repair and invariants

The frontend runtime and PgBouncer builder require vendor-fixed Brotli/curl minimums from the existing supported Alpine repository. Official x86_64 and aarch64 indexes contain all three versions. The reviewed base-image digests, existing libexpat/libx11 fixes, PgBouncer 1.26.1 source checksum, compiler version/provenance, non-root users and TLS verification are preserved.

The latest released npm CLI is still 12.2.1; 12.2.2 is unavailable. Its bundled node-gyp permits undici `^6.25.0`, so the builder applies the released compatible 6.29.1 package to that one vendored dependency. This is a **downstream npm-tool dependency patch**, not a stock npm version upgrade. Lifecycle scripts are disabled during packing; the tarball's SHA-256 is pinned and its registry SHA-512 independently verified. The complete old package is replaced, the complete new MIT license retained, old/new versions checked and explicit patch provenance saved in the build image. Application dependencies and lockfiles stay unchanged.

Compose pins official RustFS 1.0.4 at multiarchitecture index `sha256:ed9210d6411d77ffceb70d94b964a3cb7d225983368bc25bebe01e995293dcc2`. Both architecture package databases contain the fixed libraries. User 10001, entrypoint, command, credentials interface, service/DNS name and volume settings retain their existing contract. The official five-commit comparison changes trust/dependency/release material without changing production disk/object implementation files. Dependency changes still require native storage checks; no patch-version rollback guarantee is inferred.

Native qualification also exposed an inherited false-health failure: BusyBox wget resolved the default probe's `localhost` to `::1`, while the default Nginx configuration listens on IPv4. The baked probe now targets `127.0.0.1:80`; intervals/timeouts remain unchanged. Hostinger's separate TLS override stays unchanged because the production configuration explicitly listens on both address families; only its explanatory comment is corrected.

Only two Dockerfiles, one Compose pin and the matching image-inventory fixture constant change product/build configuration; the additional Hostinger change is comment-only with identical parsed configuration. The image audit, severities, scanner pin, supported-platform checks, report-identity validation and all refusal paths remain intact. No application API, authorization rule, financial value, database migration, visual reference or GitHub workflow is changed.

## Qualification and evidence

The final combined image inventory, PgBouncer manifest, compiler-installation and supply-chain regression cohort passes **92 tests**, with no skips (24.15 seconds). These are existing behavioral/security contracts, not newly added string assertions mirroring the patch. The frontend author also verified a private patched-npm install and build: 845 modules, 437.3 kB raw / 128.9 kB gzip within unchanged budgets. All 89 files are byte-identical across the preceding candidate, patched host build and actual Node 22 compatibility build with the retained qualified compiler.

A defensive synthetic loopback response reproduces undici 6.29.0's unhandled process crash despite a caught fetch rejection; 6.29.1 survives with a controlled rejection. Both host Node 24 and actual retained-image Node 22.23.3 reproduce the before/after result. The first Node 22 harness mount failure is preserved separately and is not a product finding.

The exact pinned RustFS 1.0.4 image and exact authored PgBouncer builder/runtime images scan with zero HIGH/CRITICAL rows. The native Node 22/npm builder probe and Nginx runtime-stage probe also scan clean. Those frontend probes reuse the qualified compiler; they do not reproduce the complete multistage Go build. Nginx configuration, UID 101, exact HTML bytes and API proxying pass; native before/after health commands bind the loopback correction to the final probe.

The six-group actual S3 initialization/persistence/refusal suite passes. A separate bounded upgrade test verifies three synthetic objects written by 1.0.3 remain readable by the restored exact 1.0.4 image, including a 4 MiB object, binary/empty data, content types, size limits and SHA-256. The reused backend supplies byte-matched storage/bootstrap helpers and SDK compatibility only; its unrelated older PDF package does not qualify a new backend image. No new-to-old disk rollback claim follows.

Initial probe mount/source-overlay, Docker proxy/TLS and cache-tmpfs errors are preserved as harness failures, separately from the real default-health defect. Local VFS space is bounded: all pre-task images/data are retained, and only task-created images may be exported with hashes and removed after use. A small-layer probe does not qualify a complete new application image. Fresh combined hosted builds/scans and human/Lead review remain required before integration.

The [source/evidence receipt](evidence/IMAGE-GATE-REPAIR-2026-10-26.json) binds five final native scan identities, all five changed source/fixture/comment files and the preserved original failure. Bounded child receipts are retained in `/workspace/artifacts/image-gate-2026-10-26/`; raw logs, metadata and isolated probe artifacts use task-specific `/tmp` paths. No customer invoice, production data, provider credential or live deployment is used. AI assistance covers implementation, test/research assistance and independent-agent review; it is not human approval.

## Adversarial review and alternatives

The important objection is that modifying a bundled npm dependency creates a downstream toolchain variant. A nonexistent npm release cannot be pinned, and an unreviewed downgrade or suppressed advisory would not repair the contract. The narrow same-major patch is justified by the parent's supported version range, verified upstream package/license, explicit provenance, exact version guards and genuine behavior/compatibility evidence. Independent review checks those controls; human/Lead review remains separate.

A supported official RustFS patch is preferable to a new wrapper image or storage-provider replacement. A clean metadata package list alone is insufficient: the actual image must scan clean and storage behavior must be checked. No severity, vulnerability or image target is waived.

## Failure, rollback and backlog

For frontend/PgBouncer, revert the repair commit and rebuild only as an explicitly reviewed rollback; doing so restores the known vulnerable packages and must not be treated as release clearance. No SQL migration or data rewrite is introduced. Before a real RustFS cutover or downgrade, quiesce writers and retain a verified backup. One synthetic old-version write/new-version read does not establish new-to-old disk compatibility or safe production downgrade.

The immediate maintenance prompt is: **repair all observed image-package findings, verify official artifacts and native compatibility, and obtain a clean complete image gate at the delivered revision without deleting targets or weakening refusal paths.** The project-body reflow (CTX-02), switch-write outcome (CTX-03), picker containment (CTX-04) and Suppliers capture/DOM investigation (CTX-05) remain in the ordered UI backlog.
