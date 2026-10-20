# SYS-04 / V05b — Runtime repairs and complete declared image inventory

AI-assisted implementation, native compatibility checks, security evidence and
documentation. Runtime repairs and the declared Compose/Kubernetes/build inventory
are implemented, pending exact-head CI and human/lead review. **Release remains
blocked** by build/service findings and unavailable images; no waiver is included.

## Resulting behavior

The API/worker image uses pinned Python 3.14 Alpine with the same Python dependency
versions. Tesseract, English/orientation models, DejaVu fonts, tini, UID/GID 10001,
private shared storage and parser process limits remain. `gcompat`/`libstdc++` support
the native PDF dependencies; the existing DejaVu path is preserved. This changes
libc/platform dependencies and requires representative workload review before rollout.

The final origin upgrades only Alpine libexpat, **2.8.5-r0 → 2.9.0-r0**, addressing
newly observed HIGH `CVE-2026-77214`. NGINX/base pin and frontend source/lock are
unchanged. Its static files are byte-identical to the earlier image.

Both Compose PostgreSQL references now pin the existing official multi-platform
digest `sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea`.
The tested amd64 image is the same previously resolved PostgreSQL 16.15 image;
this makes selection reproducible without changing its bytes or clearing its CVEs.

## Fail-closed audit coverage

[The audit runner](../../scripts/image_audit.py) discovers all image fields in
Compose/Kubernetes YAML, including nested containers, and records every source
location. Known application templates map to the just-built CI images. Four
Dockerfile FROM inputs map to backend/frontend build and final outputs; unexpected
stages, unpinned bases and unmapped templates block. CI loads both build stages
and audits all **eight targets / fifteen manifest references** even if another
check failed. Artifact upload remains unconditional.

Each image is inspected and scanned by immutable ID on linux/amd64. Both a valid
CycloneDX SBOM and a valid OS/package audit must name that ID and agree with scanner
exit status. Missing/malformed/stale evidence, unsupported OS, tool errors, unavailable
images and mutable service references cannot pass. One failure does not hide later
images. A new run publishes a non-passing marker before inventory/scanning, so
invalid inputs cannot retain a prior passing summary. No severity or budget changed.

Full image configuration remains in memory; raw scanner JSON stays in a private
temporary directory. Published reports omit ImageConfig/environment/history and
image labels, retaining advisory/package evidence, identity and raw/sanitized hashes.
The legacy `scan_images.sh` remains an explicit-image diagnostic; CI uses the full
inventory runner. Browser/CI-tool/maintenance containers outside these manifests
and GitHub Action internals are not covered. Template mappings do not certify
already-deployed GHCR artifacts; audit the actual promoted/deployed digest separately.

## Measured image outcomes

[Complete sanitized evidence](evidence/SYS-04-IMAGE-COVERAGE-2026-10-17.json) contains
every remaining advisory/package/version row, all image identities and source hashes.

| Target | HIGH / CRITICAL | Outcome |
|---|---|---|
| Final API/worker | **0 / 0** | Passed; image `17cde57af627` |
| Final frontend | **0 / 0** | Passed; image `6bda2b6d23b7` |
| Backend build stage | **4 / 0** | Fails: pip-vendored msgpack, setuptools and urllib3 |
| Frontend build stage | **18 / 0** | Fails: npm dependencies and native TypeScript compiler's Go dependencies |
| Pinned PostgreSQL | **24 / 1** | Fails: gosu Go dependencies; not a PostgreSQL query-engine verdict |
| MinIO server / mc | Unscanned | Pulls denied/unavailable; no clean result |
| Optional PgBouncer configured tag | Unscanned | Manifest unknown; no clean result |

The full inventory gate exits **1**, preserving all **47 remaining advisory/package
rows**. The previous Debian backend's 70 HIGH + 1 CRITICAL snapshot used the same
scanner database; those old images remain unsafe to promote without review. The
new API image is **423,597,061 bytes**, versus 505,292,631: **16.17% smaller**.
This measures image size, not RAM, throughput or minimum-hardware acceptance.

## Native compatibility and regression evidence

- Two CI-coverage regressions failed before workflow changes; fourteen initial
  runner cases passed. The expanded cases test vulnerable/false-zero/tool-error
  exits, missing/malformed evidence, mismatched SBOM/audit identities, unsupported
  OS, later-image continuation, private-config removal and invalid-inventory replay.
- **94 PDF/OCR checks passed inside the final Python 3.14 image**, 104.11 seconds,
  29 existing synthetic HMAC warnings. Real scanned-invoice OCR, text extraction,
  invoice PDF/XML, page rendering and kernel CPU/memory/timeout recovery ran with
  no skipped OCR dependencies, UID 10001, read-only root and dropped capabilities.
- **88 required PostgreSQL checks passed**, 45.96 seconds, 117 existing synthetic
  HMAC warnings. Exact CI file list; migrations on pinned PG16.15; application role
  NOSUPERUSER/NOBYPASSRLS. Dump/restore matched every table's count and ordered data
  hash across **113 tables**. This synthetic same-cluster clone does not certify
  production keys or external document-volume restore.
- Final-image HTTP upload → separate native persisted handler → parsed capture →
  source/page download passed. A read-only volume returned 503/retry guidance
  without new document/run/job/usage rows. API restart preserved source/page bytes.
  All **11 final-image shared-volume checks passed**, including legacy ownership
  refusal and preservation; no customer ownership was changed.
- Actual TLS discovery/token/JWKS with certificate verification, Host/SNI and RSA
  ID-token validation passed on Alpine. Synthetic resolver/final-socket mapping
  targets an owned loopback server; private DNS caused no connection and private
  token metadata caused no POST. This is not external enterprise IdP acceptance.
- Actual non-root NGINX with the production TLS config passed certificate-verified
  HTTPS, HTTP redirect, root/asset security headers and API/document proxy checks.
  Private fixture key used root:101/0440; read-only root, dropped capabilities and
  no-new-privileges. The upstream was a synthetic HTTP fixture. All static files
  match the prior image; no frontend bundle/visual budget or snapshot changed.
- **68 local deployment/docs/supply-chain/audit checks passed**, 8.85 seconds,
  including 21 image-audit cases; **3,821 backend tests collected**. Ruff/format
  checked 810 app/test/audit-helper files; action pins and the control view passed. Full
  unrelated backend/browser suites were not repeated locally; hosted CI remains
  separate. No source test, timeout, quota, audit severity or assertion was weakened.
- Current-tree structural PII, document links and source/image/evidence identities
  passed. The owner-held denylist remains empty; historical PII is not cleared.
  Final CLI-only formatting preserves the AST of the native-executed audit runner.

Three fixture corrections are recorded: TCP readiness avoids PostgreSQL's temporary
bootstrap socket; restore inventories compare canonical table order because UNION
ALL does not guarantee it; the origin probe reads both Cache-Control header values.
These changed the harness, not application behavior. Only owned synthetic containers,
volumes/networks and temporary candidate images were used/cleaned up.

## Remaining vendor/owner boundaries and rollout

Pip 26.2.1, npm 12.2.0 and TypeScript 7.0.2 are still the current upstream releases
observed here. Listed fixes to their vendored dependencies do not qualify a manual
vendor-directory rewrite or an arbitrary compiler downgrade. Obtain a supported
patched distribution/build or lead/security review of exact applicability; no VEX
or exception was invented. Gosu findings likewise remain open in PostgreSQL.

[MinIO upstream](https://github.com/minio/minio/blob/master/README.md) now marks the
community repository unmaintained and source-only. Registry denials alone do not
prove deletion. A maintained S3 artifact/provider, licensing/support contract and
compatibility/restore plan need owner/lead selection under [Decisions §29](../DECISIONS-NEEDED.md#29-residual-nativebuild-image-findings-v05b-2026-10-16).
No store replacement, credential request or activation is included. PgBouncer
`v1.23.1-p0` has a manifest, but is unqualified and not selected: upstream's default
5432/config-generation contract must be checked against the template's 6432 and
read-only root before choosing a supported pinned image.

After review/security clearance, rebuild/publish API and worker together from the
same qualified artifact and recreate containers. No schema/data-format change or
backfill. Rollback uses the previous code/image with compatible UID/volume data,
but reintroduces its recorded runtime vulnerabilities; it is not security clearance.
Real customer documents, production KEK restore, ARM, provider/edge integration,
minimum hardware and human/lead acceptance remain unproven. Legacy root-owned
nonempty volumes still require the SYS-03 operator-reviewed migration.

Runtime fixes and inventory engineering are implemented; build/service qualification
stays open. **CAP-RETRY is the next unblocked repair** while those vendor/owner inputs
are pending. No merge, production deployment or release approval was performed.
