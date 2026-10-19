# CI image repairs and remaining storage decision

**19 October 2026 · SYS-04 / V05b · implemented, pending new-head CI and Lead review.**
AI-assisted investigation, implementation and qualification by independent agents.
[Sanitized evidence](evidence/CI-IMAGE-REPAIR-2026-10-19.json).

The combined local audit now reports **nine clean build/runtime outputs, zero
HIGH/CRITICAL rows, and two unavailable MinIO references**. Its exit remains **1**.
The image gate is still required and blocked. No ignore, VEX, severity downgrade,
scanner exception, retention change, production migration or deployment was made.
The earlier [47-row inventory](evidence/SYS-04-IMAGE-COVERAGE-2026-10-17.json)
is preserved as historical evidence; its package occurrences are repaired in the
new qualified images, rather than deleted from the record.

## Repairs

| Component | Repair | Compatibility evidence |
| --- | --- | --- |
| Backend builder, 4 HIGH | Replace the active inherited pip installer with digest-pinned official uv 0.13.1. Remove unused pip before processing dependencies; keep uv in the audited builder, with MIT/Apache notices. | Exact 64 installed distribution names/versions unchanged; all 421 application source files match; shipped Python 3.14 native checks and crypto/PDF/image smoke pass. |
| Frontend builder, 18 HIGH | npm 12.2.1; rebuild exact TypeScript 7.0.2 source with Go 1.27.2 and supported x/text 0.41.0 module update. Retain source/module provenance and linked licenses. | Strict application typecheck, matching positive/negative diagnostics and six Unicode cases; 656 upstream tests/subtests pass, eight fixture/submodule skips. All 86 compiled application assets are byte-identical to the official compiler baseline. |
| PostgreSQL, 24 HIGH + 1 CRITICAL | Rebuild unchanged gosu 1.19 source using Go 1.27.2 on the same digest-pinned official PG16 base. Preserve release metadata so the scanner positively identifies gosu v1.19.0. | PostgreSQL 16.15 executable/init files, entrypoint, UID/GID70, volume format and authentication defaults unchanged; original PG16 volume restarts with exact row hashes, correct-password acceptance and wrong-password refusal over external TCP. |
| PgBouncer, invalid old tag | Build checksum-verified upstream 1.26.1 on the pinned same-vendor edoburu runtime. Replace the inherited executable in both builder/runtime. | Actual binary version asserted, SCRAM and transaction pooling exercised; authenticated readiness fails during DB outage and recovers after DB/pooler restart. |

The latest ordinary pip release still vendors the affected packages. The latest
ordinary TypeScript npm binary still embeds its older Go runtime. Upgrading their
version labels alone does not repair these occurrences. No vendored source files
were manually rewritten and no compiler downgrade was selected. Source archives
and base images are checksum/digest pinned. Used build tools remain visible in
the audited stages; only unused inherited pip is removed before use.

PgBouncer's published same-vendor 1.26.0 candidate scans clean but has upstream
SCRAM authentication-bypass/DoS defects fixed in 1.26.1: CVE-2026-107350,
CVE-2026-107349 and CVE-2026-107348. Scanner silence alone was insufficient.
See the [upstream release](https://www.pgbouncer.org/2026/10/pgbouncer-1-26-1).
Its manifest also now explicitly selects port6432, provides bounded writable
memory configuration under a read-only root, and uses authenticated SQL readiness.
Kubernetes fsGroup/fleet behavior still needs deployed qualification.

CI now loads all nine Dockerfile outputs, including the new compiler/service
builders. The inventory rejects missing, added, mutable or unmapped stages and
records external pinned COPY inputs, including uv. Release builds publish the
four final artifacts; the optional images overlay consumes the same commit's
PostgreSQL artifact. Release publication remains distinct from promotion.

## Validation and exact scope

- Combined audit: all nine outputs clean; both existing MinIO references
  unavailable/unpinned; full gate exit1. Pinned Trivy and current 19 October DB;
  exact inspected IDs and sanitized SBOM/audit hashes are in the evidence JSON.
- Infrastructure/inventory/supply-chain/deployment controls: **102 passed**;
  the final selection including documentation truth is **109 passed**.
  Backend collection: **4,205 cases**, not 4,205 executed passes. Full Ruff/format
  and auditor lint/format pass from the backend configuration directory. Mypy
  passes all **421 application source files**.
- Backend native selection: **163 passed**, with one default-config harness
  failure caused by supplying SECRET_KEY; removing that harness variable for the
  isolated default-config case yields **one passed**. No product/test assertion
  changed. The final notice-bearing image preserves the exact source and 64
  distribution versions; both final images were freshly rescanned.
- Frontend final compiler/builder/runtime all clean. Bundle **434.6 kB raw /
  128.3 kB gzip**, within unchanged 460/135 limits. No SPA source or browser test
  changed; browser tests were not rerun locally for identical compiled assets.
- PostgreSQL: **140 required tests, six growth checks and eight concurrency
  scenarios with zero errors**, plus dump/restore of **114 tables / 31,316
  synthetic rows**, on the preceding binary before release-metadata refinement.
  The exact final runtime then passed original-volume/hash/auth/ownership checks.
  Final runtime ID is unchanged by retaining curl/bash in its audited builder.
  Optional known SYS-06 reflection drift remains separate and unmodified.
- PgBouncer: **18 targeted tests**, baseline four failures; native two-tenant
  application traffic, **96 concurrent HTTP reads**, **160 RLS transactions**,
  wrong-password refusal, outage and restart recovery pass. Application traffic
  used host Python3.12; this is not a live Kubernetes release acceptance.

Initial fixture/proxy/port/default-environment mistakes and a root-directory Ruff
invocation with incorrectly resolved relative ignores are retained in temporary
logs, uncredited. Corrected executions pass. No assertions or budgets were
weakened. All uniquely owned native fixture volumes/containers and credential
files were removed; existing object-store volumes were not accessed.

Pre-repair head **d2f8d765ed5a999e0b9b883a007ee1cc547ba61f** completed [CI
39226522305](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39226522305)
with backend, PostgreSQL, frontend, browser, lint and PII success; only the image
audit failed. Supply-chain **39226522231** passed. Those are prior-head results;
this repair still needs its own hosted CI and human review.

## Remaining decision and development prompts

**S3 provider choice is pending.** [Decision §29](../DECISIONS-NEEDED.md#29-residual-nativebuild-image-findings-v05b-2026-10-16)
requires owner/lead selection of a maintained artifact/provider and review of
licensing/support and restore compatibility. The archived MinIO community images
and mc helper remain in active configuration until that choice is supplied.
They have not been removed from the audit to obtain green CI.

A concrete tested candidate is **RustFS 1.0.3**, Apache-2.0, immutable index
`sha256:9ca9559533b537d10482fe923288b676ad08f92a7069d9a17327fbae3cab688b`.
Its native image scans zero HIGH/CRITICAL. Nine checks cover current application
S3 writes/reads/limits/digests/delete, six unauthorized operations, idempotent
bucket initialization, same-volume restart and synthetic API export/import into
an independent new volume. Twenty-two bootstrap tests and four native legacy
guard cases pass. Attached registry provenance aligns with the platform digest;
independent signature verification and a support SLA are not claimed.

Proposed integration uses a **separate new volume**, keeps the old MinIO volume
read-only for a guard, and refuses startup on any hidden/visible entry or
inspection error. The archived mc helper is replaced with the audited backend's
boto3 initializer. Production secrets must be mapped atomically to RustFS,
initializer, API and worker. No in-place MinIO disk compatibility is assumed.
Existing data requires a frozen source/DB inventory, backups, verified streamed
copy and restore reconciliation before an explicit reviewed cutover. Synthetic
RustFS-to-RustFS restore does not qualify a historical MinIO migration.

**Storage prompt, after owner selection:** Integrate the selected maintained
provider with authenticated idempotent bucket initialization, a separate volume
and fail-closed legacy-data protection. Preserve operator credentials and old
data, refuse permission/network failures, test fresh/restart/recovery and real
application S3 operations, and run the complete image gate with no exemptions.
Keep production migration disabled pending verified reference/restore evidence.

**CI-HOST-TSC follow-up:** Plain host `npm ci` still downloads TypeScript's
official 7.0.2 executable with its old embedded Go1.26.4. The Docker security
rebuild repairs the image gate; it does not replace that host executable.
Prompt: make host CI lint/build jobs consume the same verified rebuilt compiler
through a shared reproducible artifact, verify its source/version/checksum and
licenses before execution, and preserve the existing diagnostics, browser and
bundle controls. Do not describe the entire hosted toolchain as clean until
that separate path is qualified. This is a known exposure, not a currently
observed failing host check.
