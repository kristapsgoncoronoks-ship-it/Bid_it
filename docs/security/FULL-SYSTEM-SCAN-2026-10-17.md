# Full-system scan — 17 October 2026

**Subsequent repairs:** [SYS-01 OIDC destination validation](SYS-01-OIDC-DESTINATIONS-2026-10-17.md)
[SYS-02 Compose readiness](SYS-02-COMPOSE-READINESS-2026-10-17.md)
[SYS-03 fresh storage-volume permissions](SYS-03-STORAGE-VOLUME-2026-10-17.md)
and [SYS-04 runtime repair/image coverage](SYS-04-IMAGE-COVERAGE-2026-10-17.md)
are implemented with local evidence, pending CI/human review. This report and JSON
retain scan-time observations; use the findings register for current work status.

**Verdict: release blocked.** The scan confirmed an OIDC destination-validation
vulnerability, capture retry provenance corruption and two deployment failures,
in addition to open native-image findings. Clean dependency audits and passing
regressions do not clear these defects. No application fixes or deployment were
performed in this scan; the work is logged for sequential repair.

AI-assisted static review, synthetic reproduction, scan orchestration and report
drafting. Application baseline: `716153ccb82ab9ba6dbb7cad5a86e5d6d77995c8`, draft
[PR #64](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/pull/64).
[Complete sanitized machine-readable evidence](evidence/FULL-SYSTEM-SCAN-2026-10-17.json)
contains every image advisory/package/version, all 42 Bandit and 32 configuration
candidates, probe outcomes and audit hashes. No raw secrets or historic identifiers
are published. The [findings register](../reviews/FINDINGS-REGISTER-2026-10-17.md)
and [backlog](../BACKLOG.md) retain the work order.

## Scope and limitations

Scanned the 1,634 tracked paths at the baseline, backend application source,
complete declared Python runtime/development and npm lock graphs, workflow and
security controls, tracked-source secret/configuration candidates, Docker/Compose
and Kubernetes configuration, four rebuilt/resolved images and all fetched branch
history. Exercised real PostgreSQL authorization/concurrency checks and synthetic
native-image HTTP capture/worker/download behavior. CI results identify their exact
source head; browser route mocks prove client behavior, not every live backend or
external-provider outcome.

This is broad engineering evidence, not proof of absence of all bugs. No production
host, customer data, private IdP, paid processor, accountant installation or actual
VPS/key-provider recovery was accessed. Branch-protection inspection returned GitHub
403, **Resource not accessible by integration**: settings remain unverified rather
than proven absent. No ports on external systems were probed. Unavailable service
images and production-only acceptance gaps remain explicit below.

## Confirmed findings and repair prompts

### SYS-01 · P1 security — OIDC token endpoint permits private destinations

`backend/app/services/oidc.py:234` sends token exchange requests using a plain
`httpx.AsyncClient`. `finish_login` passes `disco["token_endpoint"]` without the URL
check used for discovery/JWKS. Discovery content from a configured malicious or
compromised provider can point at an HTTP loopback/private service. This occurs
before ID-token validation and includes the OAuth client secret, code and verifier.

**Reproduction:** replace only discovery metadata with a synthetic response whose
token endpoint is a loopback HTTP server. Invoke the real `finish_login` and token
exchange with synthetic credentials. The local server received all three fields;
login then failed for missing `id_token`. No production credentials or arbitrary
external/private host was contacted. This proves the unguarded destination and
sensitive-field transmission, not cross-tenant login or metadata-service compromise.

Discovery/JWKS also use preflight DNS checks followed by ordinary HTTP resolution;
the pinned transport already used by webhooks is absent. DNS-rebinding exploitation
was not reproduced here and must remain distinct from the confirmed token issue.

**Prompt:** validate every OIDC network destination and configured/discovered issuer
relationship before I/O. Reuse the vetted public-address transport where compatible,
require HTTPS and reject credentials/private/mixed DNS answers, pin the connection,
and keep redirects controlled. Add regressions for token-endpoint loopback/metadata,
DNS changes, issuer mismatch and valid public IdP exchange, proving no code/secret/
verifier leaves on refusal. Preserve PKCE, one-use browser state, key/claim checks
and supported enterprise configuration. Document any explicitly approved private
IdP deployment contract rather than silently allowing arbitrary internal access.

### SYS-02 · P1 deployment blocker — Compose readiness executes removed curl

The rebuilt backend image runs as UID 10001 and contains Tesseract but **no curl**.
Both `docker-compose.yml:90` and `docker-compose.hostinger.yml:86` still specify
`["CMD", "curl", "-fsS", "http://localhost:8000/health/ready"]`. Rendered generic
production configuration inherits that command. Worker/frontend dependencies wait
for backend `service_healthy`, so image startup alone cannot make the documented
stack healthy. The Dockerfile's Python liveness check does not replace a Compose
override. This is a source/configuration integration defect introduced across the
image-hardening boundary; not an actual production outage observation.

**Prompt:** replace every affected readiness command with a bounded Python HTTP
probe already supported in the image, preserving `/health/ready`, timeout, failure
exit and dependent startup ordering. Verify base, generic production, Hostinger and
migration/image overlays after rendering. Start the real image with healthy and
unavailable databases and prove workers/frontend unblock only when readiness
succeeds. Keep curl removed and security gate strength unchanged.

### SYS-03 · P1 storage failure — fresh local volume is unwritable

The Hostinger stack mounts a named volume at `/app/var/storage`. The image prepares
and owns `/app/var`, but does not prepare the mount target itself. On a new Docker
volume, that target was created owned by **UID 0**. A real image invocation under
**UID 10001** calling `LocalStorage.put` received **PermissionError**. Existing
manually prepared volumes can mask this on upgrades; existing customer volumes were
not inspected or changed. The isolated probe volume was removed after the check.

**Prompt:** define a safe fresh-volume ownership contract for the shared API/worker
storage target. Prepare the mount target in the image or use a narrowly scoped
initialization step without making application processes root or directories world
writable. Test a new named volume and a pre-existing volume, original/page writes
and reads across API/worker, restrictive permissions, storage refusal and restart.
Do not recursively change existing customer data without an explicit migration plan.

### CAP-RETRY · P1 confirmed consistency defect — stale worker writes provenance

Previously suspected in the findings register; now reproduced against PostgreSQL 17
with the non-superuser/NOBYPASSRLS role and the actual API/native job path.
`retry_upload` creates unkeyed jobs and clears fields, while `extract_upload` commits
`parsed` before recording provenance and has no per-capture attempt fence.

A synthetic capture initially had **17** provenance rows. Pause the retry worker
at `record_fields` after its parsed commit; submit another retry; the capture is
queued with zero rows. Resume the older worker: it writes **17 stale rows** while
the newer attempt is queued. Run the newer job: it finishes with **34 rows**.
Separate real database sessions, persisted jobs and the actual parser were used;
only the persistence barrier was injected. No legal invoice or customer record
was modified. Simultaneous double-click scheduling remains an additional case to
cover, not a separately claimed reproduction.

**Prompt:** introduce an atomic attempt identity/claim and generation fence for
retry and extraction. An older worker must not publish draft, pages, provenance,
progress or learning observations after a new attempt/reset/save. Bind job intent
to the attempt, converge transport replays, preserve reviewed corrections unless
explicit discard was authorized, and keep audit/access/tenant guarantees. Reproduce
this interleaving and concurrent retries on PostgreSQL before the repair; verify
failure/recovery and normal native processing afterward.

### SYS-04 · P1 supply-chain/deployment gap — service images not covered

The final-image CI gate currently audits API/worker and frontend application images,
not the PostgreSQL, MinIO/init or optional PgBouncer service images. The resolved
`postgres:16-alpine` image has **24 HIGH + 1 CRITICAL / 25 IDs**, all located in
`usr/local/bin/gosu` Go dependencies. The critical record is CVE-2025-68121, installed
Go stdlib v1.24.6; the scanner lists fixed toolchain versions. These are findings
in the image helper, not proof that PostgreSQL's query engine is vulnerable.

`minio/minio:latest` and `minio/mc:latest` could not be pulled anonymously (denied;
direct authenticated public-token manifest requests returned 401). Their contents
were **not scanned**; this is an availability/authorization gap, not proof the
repositories are deleted. The template's `edoburu/pgbouncer:1.23.1` returned manifest
unknown / direct registry 404. Do not claim a runnable optional deployment from that
tag. Mutable `latest`/service tags also prevent reproducing a future deployment from
source alone. Exact resolved PostgreSQL digest is in the JSON.

**Prompt:** inventory every actual supported deployment image, verify registry/tag
availability and ownership, select maintained compatible immutable digests, and
extend evidence/fail-closed scanning to state services and helpers. Assess all gosu
advisories by exact version/use before choosing the repair. Prove non-root startup,
PG migration/RLS, backup/restore and object-storage behavior on the same images.
Do not infer private registry credentials, activate a replacement store, or waive
unknown images. Treat provider/product replacement as a separate reviewed choice.

### SYS-05 · P2 configuration review — Kubernetes and hardening gaps

Trivy produced **32 candidates: 2 HIGH, 14 MEDIUM, 16 LOW**. The complete per-file/rule
inventory is in the JSON, with dispositions. These are template checks, not 32
confirmed exploit paths. Migration/frontend templates lack some explicit privilege,
capability and writable-filesystem restrictions that API/worker manifests already
carry. Kubernetes is not deployed in this environment.

The two ConfigMap “sensitive content” alerts match numeric byte/time/port/client
settings: reviewed false positives, not secrets. A frontend “runs as root” alert
reflects absent manifest user declarations; the final image explicitly uses nginx
(non-root). Low UID/GID/registry-policy warnings need platform review. Read-only
root filesystems require supported writable mounts; applying a blanket switch can
break startup. Hostinger also does not carry the generic production overlay's
explicit capability/no-new-privilege controls, although its image uses a non-root UID.

**Prompt:** review each recorded rule against the supported platform and actual
image. Add justified explicit user/group, no-new-privilege, capability and seccomp
settings plus needed writable mounts; verify NGINX low-port/cache/PID handling and
migration behavior. Add meaningful rendered-config/runtime checks, preserve necessary
write paths, and document false positives individually without suppressing real risk.

### SYS-06 · P2 tooling mismatch — PostgreSQL autogenerate proposes unsafe FK changes

Fresh SQLite migration/check passed; PostgreSQL migrations and 88 required PG checks
passed. A separate PostgreSQL `alembic check` reports four remove/add FK pairs:
column-subset `ON DELETE SET NULL (project_id/issuer_id)` differs from portable model
metadata `SET NULL`. The existing DB-017/018 regression explains why the subset form
must preserve `org_id`. This is a dialect/model representation mismatch, not proof
of a corrupt migrated database. Blindly applying the generated replacements can
restore the known tenant-column nulling defect.

**Prompt:** reconcile or explicitly account for the supported dialect-specific FK
representation in migration tooling without weakening tenant constraints. Make
fresh SQLite and PostgreSQL schema-drift checks meaningful, with seeded real drift
still failing. Retain the regression that deletes only the intended link column;
never replace PostgreSQL subset actions just to silence autogenerate.

### CAP-RESOURCE · P2 confirmed read pattern — other uploads still buffer first

**18 October repair:** all eleven recorded handler reads are now bounded by the
existing per-purpose caps, with upload/form cleanup and native refusal/recovery
[evidence](CAP-RESOURCE-HANDLER-BOUNDS-2026-10-18.md). The original inventory and
probe below describe the scan baseline. Native measurement also confirmed full
2 MiB spooling before a 1 MiB handler cap; **CAP-INGRESS** now tracks the remaining
pre-parser/aggregate resource work. [Subsequent CAP-INGRESS API controls](CAP-INGRESS-BOUNDARIES-2026-10-18.md)
now bound wire bytes, parts/headers and active requests, with byte-aware browser
packing and native rolled-spool/recovery measurements. Aggregate qualification
remains CAP-CAPACITY. The reproduced Vite proxy error, SYS-10, has a subsequent
[development-only repair](SYS-10-DEV-UPLOAD-PROXY-2026-10-18.md) with actual-Vite
protocol/TLS and native browser/worker recovery evidence, pending CI/human review.
Subsequent [CAP-CAPACITY ingress work](CAP-CAPACITY-INGRESS-2026-10-18.md) adds
body-only deadlines, shared origin admission and streaming, with measured
four-worker/authorized-base64 evidence. It also repairs CAP-REGISTRY's reproduced
concurrent document-metadata failure. [CAP-DISK-FULL](CAP-DISK-FULL-2026-10-18.md)
subsequently repairs misleading parser capacity errors and verifies bounded local
ENOSPC cleanup and same-key recovery. Fleet/headroom, shared disk pressure across
workers, parallel workers and object reads remain open; the broad resource item is not closed.

An AST/source inventory found **11** API upload reads without a size argument across
reconciliation, expense imports/receipts, logos, received attachments, project
documents, transport imports/estimates and inbound email. Most check size only after
reading. The existing multipart body middleware covers issued-invoice attachments,
not all these paths. The R017 bounded capture helper is not used everywhere.

A safe direct-route probe shrank the reconciliation cap to 8 bytes and supplied a
9-byte synthetic file. The route requested `read(-1)` and only then returned 413;
authorization preconditions were stubbed for this read-boundary probe. No giant
allocation, production request, OOM or external ingress exploit was attempted.
Reverse-proxy limits reduce exposure on some deployments but do not prove the
per-file service limit or a global concurrency/spooling ceiling.

**Prompt:** extend bounded cap+1 read/close behavior to each recorded supported upload
surface with its own file kind/budget, preserve visible refusal/recovery and audit,
and test unknown-length/oversized/error paths without allocating huge fixtures.
Then separately verify actual multipart/ingress/spooling and concurrent worker
budgets. Do not conflate a post-read size check with bounded resource use.

### SYS-07 · P2 test-harness investigation — SQLite thread outlives event loop

The full local suite passed but emitted **3,218 warnings**. Most rendered warnings
concern short development/synthetic HMAC keys (22/25/30 bytes); this is not evidence
of weak deployed keys. One `PytestUnhandledThreadExceptionWarning` is an aiosqlite
connection worker calling `call_soon_threadsafe` on an already closed event loop,
reported under `test_sso_oidc.py::test_admin_config_requires_admin`. The suite's zero
failure count does not erase that teardown error. No production occurrence is
established. A targeted warning-as-error follow-up is recorded in validation below.

**Prompt:** reproduce the connection/thread cleanup issue under the relevant full-suite
ordering/xdist lifecycle, ensure owned engines/connections/background tasks are
awaited before loop closure, and prove warning-as-error isolation remains clean.
Keep production key-strength validation and explicit weak-key refusal tests; improve
ordinary synthetic key fixtures without muting real warnings or adding broad filters.

## Native images and static candidates

| Scope | HIGH | CRITICAL | Distinct IDs / status |
|---|---:|---:|---|
| Rebuilt final backend, Python 3.14.8 / Debian 13.7 | 75 | 1 | 33; no listed fixes; release gate failed |
| Rebuilt final frontend | 0 | 0 | Final image scope passed |
| Frontend build stage | 17 | 0 | 17; 13 compiler Go dependencies, 4 bundled npm |
| Resolved PostgreSQL service | 24 | 1 | 25, all gosu helper Go dependencies; previously outside the gate |
| MinIO / init / optional PgBouncer | — | — | Unavailable image references; no scan result |

Rows count package/advisory occurrences, not independently exploitable application
bugs. Scanner Trivy 0.76.0 is pinned by digest; scan timestamps, DB metadata, immutable
image IDs, package versions, fixed versions, references and every occurrence are in
the JSON. The earlier rejected Bookworm/unqualified Alpine experiments remain in
the findings register; no production base was switched. No suppression, ignore,
severity reduction, arbitrary VEX or risk acceptance was introduced.

Bandit reported **42 candidates (39 LOW, 3 MEDIUM; no HIGH)**. The medium URL-open
candidate uses hardcoded ECB HTTPS constants; the two `/tmp` candidates are intentional
worker heartbeat paths. No new exploit was demonstrated from those warnings.
Low XML-import alerts must be distinguished from untrusted invoice parsing, which
uses `defusedxml`. Remaining assertion/catch/import/constant findings stay recorded
as review candidates; no “42 vulnerabilities” claim or new ignore list.

## Historical privacy and external gaps

A separate **offline generic-secret history scan** examined 7,379 text blobs across
those 10 refs and reported zero candidates; 53 binary blobs were excluded and no
blob exceeded its 5 MiB ceiling. It sent no discovered material for external
credential verification. Its temporary text-blob export was removed after scanning.
Current tracked-source Trivy secret scan also reported zero candidates; CI's
TruffleHog verified-secret check covers the PR diff, not all history. None of these
results classifies the structural PII matches below.

The refreshed all-fetched-ref inventory scans **7,432 eligible blobs across 10 refs**
and reproduces **872 masked rows / 196 unresolved blobs**. Earlier evidence used
19 refs / 7,378 blobs; differing available refs and new docs change scope, not the
unresolved population. The initial current checkout had only 4 refs and returned
2 affected blobs; fetching all branches restored the wider population. A narrow
checkout must not be presented as history cleanup. Inventory remains private,
mode 0600, outside Git, with only summary/hash committed. Denylist has **zero**
entries: coverage is structural until the owner supplies verified inputs. No
classification, allowlist, history rewrite or deletion was performed.

Existing decision boundaries remain in [Decisions](../DECISIONS-NEEDED.md), including
private disclosure/protection, IdP/provider/ERP acceptance, production keys/residency,
legacy SMTP and credit reconciliation, accountant acceptance and broader finance
vision. A synthetic pilot does not establish production readiness or minimum hardware.

## Completed validation and remaining gates

| Check | Result / scope |
|---|---|
| Full local backend, Python 3.12.14 | **3,675 passed, 71 skipped**, 1,213.63 s, four xdist workers; 3,218 warnings separately logged as SYS-07. Skips are not counted as passes |
| Required PostgreSQL list | **88 passed**, 198.13 s; migrated PG17, non-superuser/NOBYPASSRLS role, actual 32-file CI list |
| Exact-baseline browser / visual CI | **582 + 13 passed** in run [39055569888](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39055569888); client tests include mocked routes. Duplicate local browser run stopped after this full CI result, not counted as a separate full pass |
| Python / npm advisory audits | **0 findings**; 77 resolved Python packages, complete npm lock including dev/transitives |
| Lint / types / frontend build | Ruff check/format 807 files, mypy 418 files, frontend lint/build passed |
| Frontend bundle | **435.8 kB raw / 127.6 kB gzip**, within unchanged 460 / 135 budgets |
| Migration checks | One head `0d546afada3c`; fresh SQLite upgrade/no drift passed; PG upgrade/concurrency passed, separate PG drift mismatch logged as SYS-06 |
| Native image capture | Python 3.14.8, actual HTTP/API → persisted job → parse → original/page bytes passed with synthetic PDF; private/no-store and SHA-256 match. UID 10001, no added capabilities/no-new-privileges; isolated dev SQLite/local store, one web worker |
| Runtime dependencies/config | PDF/image imports, Tesseract 5.5.0 and non-root/no-capability NGINX config check passed; production and three Hostinger overlay combinations rendered and exposed the curl failure |
| Workflow / controls / privacy | Action pins, generated 15-control view, tree structural PII and report links/evidence consistency passed; nine docs/PG-registration checks passed |
| Targeted thread-warning follow-up | **1 passed** with PytestUnhandledThreadExceptionWarning promoted to error; thread error did not reproduce alone. Full-suite lifecycle investigation remains open; two weak synthetic-key warnings remained visible |
| Baseline hosted CI | Workflow/security/dependency/credential, PostgreSQL, frontend/browser checks passed; final-image gate **failed**. Hosted Python 3.11 backend run was still in progress at report preparation; local full-backend result above is complete. New documentation-head CI remains separate |

The image hardening checks do not repair Compose's executable/volume integration
errors. The synthetic native probe used an owned writable temporary store, not the
failing fresh Hostinger mount. An initial probe with four development web workers
raced development `create_all` and exited; rerunning the isolated smoke with one
worker completed. That setup error does not establish a production failure: the
documented production command applies migrations first. Initial read-only tool
cache and container proxy-DNS issues were corrected without modifying application
requirements or bypassing TLS. Raw probe logs/history inventory stay private;
owned probe containers/volume and the temporary historical text export were removed,
and the dedicated PG test instance was stopped.

The new work is queued individually in the register. First repair SYS-01, then
SYS-02/SYS-03, image/service coverage and CAP-RETRY. Keep historical PII, human/lead
review and external acceptance gates open. No merge/deployment, waiver, production
credential change or data/history rewrite is part of this scan.
