# Alpine image maintenance — 1 November 2026

**Status: local candidate builds, runtime and image-security qualification passed; delivered-head hosted CI and human/Lead review are pending at source publication.** Publisher metadata supports four compatible Alpine package floors for three advisory IDs. This report records the source and actual-image evidence separately; it does not establish CVE-specific behavioral repair, release clearance or human approval.

The baseline is `42cb534db28850114afa54477040baadab2d5570`. The candidate worktree is `/workspace/bidit-alpine-maintenance-20261101`, branch `fix/alpine-image-maintenance-20261101`. The [structured receipt](evidence/ALPINE-IMAGE-MAINTENANCE-2026-11-01.json) binds nine actual build identities and the qualification results to the exact source hashes. The delivered commit and subsequent hosted CI are recorded in the PR; prior source CI does not qualify this change.

## Confirmed baseline inventory

Independent review recalculated twenty saved sanitized SBOM/audit hashes and matched ten image identities from run `7af49054571b43fa845d688279e8e145`. The selected findings occupy **13 advisory/role rows across seven roles**: seven musl-source rows, five zlib rows and one libxpm row. These are three advisory IDs and four targeted binary packages; advisory/role counts do not count every component or vendor rating separately. Evidence covers **linux/amd64**, with package availability checked for Alpine **v3.24 main/x86_64** only.

| Advisory | Target packages and compatible minimums | Affected baseline roles / rows |
| --- | --- | --- |
| CVE-2026-107891 | musl and musl-utils `>=1.2.6-r3` | backend, frontend, postgres, pgbouncer, backend-build, postgres-build, pgbouncer-build / 7 |
| CVE-2026-85091 | zlib `>=1.3.2-r1` | backend, postgres, pgbouncer, backend-build, postgres-build / 5 |
| CVE-2026-94287 | libxpm `>=3.5.20-r0` | served frontend/nginx / 1 |

Core musl was `1.2.6-r2` in only backend, postgres and postgres-build. All seven selected roles had musl-utils `1.2.6-r2`; the other four already had core musl `1.2.6-r3`. Enforcing both matching floors preserves the supported libc/utilities pairing. This is not proof that every affected utility is reachable.

The five selected zlib copies were `1.3.2-r0`; frontend and pgbouncer-build already had `1.3.2-r1`. Frontend libxpm was `3.5.19-r0`. Frontend-build, frontend-compiler-build and RustFS already met these selected floors and receive no maintenance edit.

The saved frontend dependency graph includes `nginx-module-image-filter → libgd → libxpm`. It establishes package ownership, not a loaded module, reachable write path or customer-triggerable vulnerability. Alpine's libxpm fixed floor differs from generic advisory wording; that discrepancy remains recorded and is not treated as a false-positive determination.

## Publisher evidence and source correction

Fresh HTTPS retrievals on 1 November 2026 matched Alpine secdb, 3.24-stable APKBUILDs and the actual `main/x86_64` binary APKINDEX. The selected versions retain the published library contracts: `libc.musl-x86_64.so.1`, `libz.so.1=1.3.2` and `libXpm.so.4=4.11.0`. Saved source/patch checksums were compared with APKBUILD values. Metadata correspondence and unchanged library names support this minimal correction; they do not prove runtime compatibility or independently verify APK signatures.

The implementation changes `backend/Dockerfile`, `frontend/Dockerfile`, `deploy/postgres/Dockerfile` and `deploy/pgbouncer/Dockerfile`: six existing APK seams gain explicit compatible minimums, and one PostgreSQL runtime seam is added before gosu smoke execution. Build seams apply the floors before dependency/source processing.

| Dockerfile / stage | Required floors |
| --- | --- |
| backend / build and runtime | musl, musl-utils, zlib |
| frontend / nginx final only | musl, musl-utils, libxpm |
| postgres / build and runtime | musl, musl-utils, zlib |
| pgbouncer / build | musl, musl-utils |
| pgbouncer / runtime | musl, musl-utils, zlib |

`apk add --upgrade` covers the explicitly named existing dependencies as well as the four targeted packages. Dependency resolution can update transitive packages. No new broad `apk upgrade` is introduced, but **the complete installed-package delta was independently compared**; this report does not claim that exactly four installed packages change. Floors use the existing supported Alpine repository channel, with ordinary APK trust/signature verification required in actual builds.

## Preserved source contracts

The implementation receipt proves all non-RUN logical instructions unchanged against the baseline, including parent digests, COPY/license paths, environment, users, entrypoints/CMD, ports and health probes. After removing only the added floors, three new `--upgrade` flags and the single new PostgreSQL runtime seam, the original RUN shell tokens match, including quoted checksum strings and security flags. `git diff --check` passed.

Python, Go/compiler, npm/undici, uv, gosu and PgBouncer release/source pins remain unchanged. The frontend Node/compiler stages are untouched. Existing backend UID/GID 10001, nginx UID/GID 101, PostgreSQL UID/GID 70 and inherited entrypoint/PGDATA contract, and PgBouncer runtime user remain unchanged at source. Actual-image checks below exercise these preserved runtime contracts.

The optional public-CA BuildKit secret remains read-only; TLS verification and source-integrity checks stay enabled. No dependency lock, application source/API definition, schema, tenant rule, migration, production data, storage configuration, provider setting or workflow is changed by the maintenance implementation. Library replacement can affect application behavior, which remains subject to runtime qualification. No library, package metadata, license or evidence target is removed. Scanner/severity selection, thresholds, ignores and refusal paths are unchanged; unrelated findings are not suppressed.

## Qualification boundary and setup failures

Nine actual build outputs succeeded on linux/amd64 with ordinary APK trust/signature checks and no verification bypass. Seven affected roles meet all 20 explicit floor contracts; two already-fixed zlib observations bring the checked total to 22. The complete APK comparison finds 16 selected-package upgrades plus four resolver/named-package updates: backend tzdata `2026d-r0 → 2026e-r0`, and PostgreSQL-builder ca-certificates-bundle `20260909-r0 → 20261020-r0`, libcrypto3/libssl3 `3.5.8-r0 → 3.5.9-r0`. No APK packages were added or removed.

All ten image identities and twenty saved SBOM/audit hashes verify. The three targeted IDs are absent from full CycloneDX reports, removing 13 advisory/role rows. All selected HIGH/CRITICAL gates pass. Nine other IDs / 11 rows remain: 14 medium, three low and one alternate HIGH provider-rating entries, plus two unscored rows. All retained rating arrays are preserved; no new ID appears. Counts have different grains: provider entries can overlap one advisory/role row. Full deltas and ratings are in `inventory-review/REVIEW.md` and its JSON receipts.

The existing image-audit module passes 90 cases, including 37 new admission controls. Those tests produce seven expected failures against the saved unchanged baseline Dockerfiles; this is source admission RED/GREEN, not CVE-specific behavioral proof. All 26 original top-level test definitions remain AST-identical. Independent overlapping retained controls pass 117 cases, and 50 other security/compiler controls pass. Full Ruff lint/format, structural PII scan and 5,341-case collection pass. The empty owner-supplied PII deny-list remains an existing limitation.

Actual-image compatibility checks pass for installed packages and ordinary musl utilities; bounded synthetic Python zlib/gzip, OCR, PDF and timezone use; real backend ENTRYPOINT/CMD startup with UID10001, tini PID1, liveness/readiness and isolated development SQLite; PostgreSQL fresh initialization/UID70 and restart persistence; PgBouncer 1.26.1 transaction-pool SQL with bad-password refusal and valid reconnect; and nginx UID101 static/proxy/TLS behavior with a synthetic group-readable key. Local-volume checks pass all 11 stages; native S3 checks pass all six groups with exact objects surviving a server-container restart. These are synthetic compatibility checks, not production sizing, external provider acceptance or a measured speed claim. Runtime qualification is explicitly composite: 18 completed checks from the preserved full attempt plus one focused nginx/TLS group on the same immutable image IDs. No single green full-harness run is fabricated. The receipt preserves each executed assertion, identity and cleanup result. No direct libXpm API roundtrip was executed; its evidence is package replacement/full-inventory disappearance plus nginx compatibility, not CVE-specific behavioral proof.

Full hosted CI for the delivered head remains pending at publication. Its eventual run IDs, exact head/merge-tree identities and results belong in the PR and `ci/` receipts. Prior PR #99's successful CI is separate historical evidence.
Two early local build attempts failed and remain preserved:

- Docker buildx could not create its state under the read-only default Docker configuration directory. This was a local setup failure before a candidate build could qualify.
- An isolated configuration omitted the environment's existing proxy settings, so APK index retrieval failed TLS checks. Subsequent package-selection errors followed inaccessible indexes; they do not establish publisher package absence.

The local setup was corrected using a writable private Docker configuration that preserves the existing proxy settings. A sanitized equality receipt records the correction without exposing proxy values or credentials. The optional CA bundle contains public certificates and no private key. No source proxy setting, TLS-verification bypass or APK-signature bypass was introduced. These failed attempts are not passing checks or product regressions.

No CVE-specific behavioral regression was executed. Delegated research hit a **model content filter**, with the general message “This content was flagged for possible cybersecurity risk.” No more specific classification was supplied. This was not an automatic approval-review rejection; the attempt was stopped without retry or rerouting. The retained research files are source-only receipts, not completed RED/GREEN evidence.

The corrected runtime fixtures retain the host-namespace/bind-mount synthetic-key and config permission failures and PgBouncer expected-message mismatch as failed setup attempts. Bad-password refusal is observed as exit 2 with empty SQL output and `SASL authentication failed`, followed by a valid reconnect. A separate initial S3 fixture failed because the build proxy was injected for local `store` traffic; its diagnostic confirms that routing, and the unchanged script passes all six groups using an empty local-runtime Docker client config. No image/source or authentication assertion was weakened. Synthetic credentials/keys and owned containers/volumes/networks are removed; no production key was read. Ordinary API success does not establish a CVE-specific regression.

## Separate inventory and rollback

The previous full inventory's other nine advisory IDs remain a separate supported-parent/applicability investigation. Global npm copies, uv/rustls, gosu and its x/sys/moby dependencies are not repaired or accepted by this Alpine maintenance. No unsupported npm parent, unrelated major upgrade or Windows-only applicability waiver is introduced. Full inventory must remain visible even where the existing selected-severity gate passes.

If qualification fails, retain the refusal and diagnose the exact image/package difference. For a later application rollback, retain these fixed floors when rebuilding a reviewed compatible source, or use a separately qualified known-good image that meets them. A plain reversion to the captured vulnerable package inventory is not a safe default rollback. No SQL/data rewrite is introduced, and this report authorizes no production migration, downgrade, main merge or deployment.

## Evidence and review

The durable source receipt links the following local artifact directories under `/workspace/artifacts/alpine-image-maintenance-2026-11-01/`; these paths are local evidence, not claims of hosted uploads:

- `publisher-review/`: current publisher receipts, package availability, independent baseline proof, checksums and blocked-research status.
- `implementation/`: exact four-file patch, hashes, stage mapping and parsed source-contract proof.
- `tests/`: original-source RED, current 90-case run, seeded controls and preserved-source/format/privacy receipts.
- `builds/`: nine successful build identities, preserved setup attempts, sanitized config/CA receipts, volume and native S3 outcomes.
- `runtime/`, `image-security/`, `inventory-review/`, `adversarial/`: actual compatibility, all ten role reports, complete inventory delta and independent source review.

The prior inventory report remains at `/workspace/artifacts/reimbursement-terminal-boundary-2026-11-01/remaining-image-inventory-review/REVIEW.md`. The implementation proof's initial whitespace-normalization mismatch was corrected to shell-token comparison; no source defect was established. The publisher retrieval script's initial local SyntaxError was corrected before its successful retrieval. Both are harness history, not application findings.

AI assistance covers autonomous source implementation, research, test assistance, independent-agent review and this documentation. Human/Lead Developer review is pending under the [AI Engineering Policy](../architecture/AI-ENGINEERING-POLICY.md); independent agents cannot supply that approval. No main merge, deployment, provider action or production-data access is claimed. Final delivered-head CI must succeed and human/Lead review remains required before any merge/release decision.
