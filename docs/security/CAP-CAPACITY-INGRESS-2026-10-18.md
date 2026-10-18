# CAP-CAPACITY — origin admission, body deadlines and concurrent registration

**Status:** ingress repair and CAP-REGISTRY implemented; exact-head CI and
human/Lead review pending. Aggregate capacity remains open. This is not release
clearance or a minimum-hardware certificate.

## Confirmed failures and resulting behavior

The old API allowed slow bodies to occupy upload slots indefinitely. New tests
produced **17 failures / two passes** before the repair. The original NGINX origin
also buffered five partial requests into five temporary files while the API had
received none of them. API admission and deadlines cannot protect that buffering.
Four new origin-source controls failed before the configuration repair.

The upload middleware now bounds **body reads**, with validated positive, finite
settings:

| Setting | Default | Outcome |
|---|---|---|
| `UPLOAD_BODY_IDLE_SECONDS` | 30 seconds | Refuse a receive wait without incoming body data. |
| `UPLOAD_BODY_TIMEOUT_SECONDS` | 300 seconds | Refuse an incomplete body after the total body-read deadline, even if it trickles. |

Expiry raises **408**, no-store, with status-check/retry guidance. Starlette closes
partial form spools, and the admission slot releases in `finally`. The final body
message ends these deadlines. Subsequent storage, transactions, worker work and
ASGI disconnect polling are not cancelled by a body timer. This preserves durable
command/receipt and partial-batch transaction ownership. It does not establish a
handler, object-storage or background-job deadline.

Both NGINX templates now use a **shared-memory limit of two upload requests per
origin instance**, across its NGINX workers. The key is a constant for recognized
API POST upload paths, and empty otherwise; the limit is not per IP or tenant.
All fifteen ingress paths, both slash forms and version prefixes are checked.
Health, status lookup and ordinary non-upload actions do not consume these slots.
Saturation returns 503 `upload_busy`, Retry-After 1 and no-store, retaining the five
production security headers. Upstream errors pass through unchanged.

`proxy_request_buffering off` and explicit HTTP/1.1 stream declared **and chunked**
bodies to the API. The existing 20 MiB origin cap is retained. NGINX's existing
**60-second idle fallback** is made explicit; the API's default idle deadline is
shorter. Native TLS testing proved that NGINX's own body timeout closes a connection
without the proposed JSON response. That ineffective draft handler was removed.
A transport-only outcome remains unconfirmed and uses command lookup/retry; it is
never relabelled as an observed API 408. This fallback can still win if API limits
are reconfigured or the backend cannot respond.

Kubernetes routes `/api` directly to the backend, bypassing frontend NGINX. Its
Ingress adds the supported streaming/HTTP-version annotations so API deadlines can
apply. **The frontend shared limit is not a Kubernetes cluster limit.** No controller
quota, replica/HPA behavior or live Kubernetes deployment was qualified here.
Compose API defaults and the ConfigMap carry the body deadlines. Six actual Compose
renders retained 20 MiB/two per-worker slots/30-second idle/300-second total.

### Shared document registration — CAP-REGISTRY

The near-limit test found another real defect: two authorized deliveries with
different Message-IDs and identical source content could both find no document
metadata. The second insert then failed `uq_documents_org_sha_kind`, returning 500.
The registry advertised an upsert but performed a select followed by an insert.

The registry now isolates its insert in a savepoint. A losing writer loads and
touches the same tenant/content/kind row; an unrelated constraint failure still
raises. The caller's pending jobs and transaction remain intact. There is no outer
rollback or internal commit. A committed winner retains original attribution; a
rolled-back winner lets the other writer create the row. File bytes, content keys,
metadata identity, RLS and retention policy are unchanged.

The SQLite rollback regression caught an error in the first prototype: legacy
sqlite3 transaction control can release a first savepoint as a commit. Before that
savepoint, this service now explicitly begins the caller's physical SQLite
transaction if it has not started. This keeps the original flush-only contract
without changing the application's global SQLite transaction mode or bypassing
ORM/tenant guards.

The deterministic PostgreSQL reproduction failed the committed-winner case before
the repair; rollback-winner and six related checks passed. Both race outcomes now
pass, including a second connection seeing no premature commit and unrelated
caller jobs surviving. Unrelated-constraint and SQLite outer-rollback tests pass.
This repair does not prove generic email Message-ID concurrency is replay-safe.

## Verification and native measurements

[Sanitized evidence, source/image identities and hashes](evidence/CAP-CAPACITY-INGRESS-2026-10-18.json)
distinguishes final results from failed prototypes and fixture corrections.

- **203 backend checks passed / 20 PG-only cases skipped locally** in the selected
  default-database run. The **entire required PostgreSQL gate passed 128 cases**
  against a fresh migrated database with a non-superuser/NOBYPASSRLS app role.
- **21 existing browser cases** passed, including surfaced timeout guidance and
  same-key retry. **19 actual-Vite HTTP/HTTPS proxy tests** passed.
- **25 checks passed inside the shipped Python 3.14 runtime**, covering real
  Starlette timeout cleanup and SQLite registry transaction ownership. Tests were
  mounted read-only because the runtime image intentionally excludes them.
- Ruff/format, mypy (421 source files), frontend lint/build, action pins, the
  15-control generated view and structural current-tree PII checks passed. Backend
  collection is **3,983**; the browser suite is **596**. Owner denylist/history
  classification remains unresolved. Bundle limits remain unchanged.

A compiled SPA → actual NGINX → current Alpine API → PostgreSQL → CSV-worker run
used synthetic data and real responses. API: **four Uvicorn workers, one CPU,
768 MiB**; origin: **two NGINX workers, one CPU, 128 MiB**; CSV worker: one CPU,
512 MiB. Faster **two-second idle / five-second total** API settings exercised the
same deadline mechanism. These are explicit fixture overrides, not the deployment
defaults or a full production topology.

| Action / boundary | Observed outcome and development contract |
|---|---|
| Direct API admission | Eight held requests distributed **2/2/2/2** across four workers. Preserve the distinction between per-worker and shared admission. |
| Origin saturation | Two held bodies reached API parsing without origin body spools. A browser capture received visible **503**; health/status remained available. Retry used its original command and returned **202**. |
| Slow browser transmission | The API emitted 408. Chromium hid it behind one transport retry; after restoring the fixture network, the original command completed once. Keep receipts authoritative rather than promising that every browser exposes an intermediate 408. |
| Idle / continuous-trickle requests | Declared and chunked requests with real disk-rolled API spools returned **408 / 408** and released admission. Preserve cleanup and recovery without cancelling post-body commits. |
| Authorized near-envelope email | Two **20,880,385-byte** JSON entities each decoded three **5,220,000-byte** attachments and returned 200/three queued/zero rejected. Six inbox records and durable jobs persisted. This replaces the earlier unauthenticated 401 capacity probe with actual authorized decode/storage evidence. |
| Durable capture completion | Exactly **two commands/two captures**; both parsed by the real CSV worker. Email extraction jobs were queued, not run by that CSV-only verification. |

The API's cgroup peak was **753,094,656 bytes (718.2 MiB)**; the origin peaked at
**10,997,760 bytes (10.5 MiB)**. No OOM/max events occurred. The API peak leaves
about **49.8 MiB** under this fixture limit: that is limited headroom, not approval
for additional queries, OCR or more concurrent bodies. Direct API admission still
multiplies across processes. Both final rebuilt application images passed fresh
HIGH/CRITICAL audits; builder/PG/unavailable-service release findings remain open.

Production-template TLS testing separately used an explicit ephemeral CA, keeping
certificate verification enabled. HTTP/1.1 saturation/timeout/recovery and production
security headers passed; HTTP/2 stream results are included in the evidence. Its
upstream is a synthetic HTTP server, separate from the full real-API workflow.

Owned fixture processes, containers, networks and object volumes are removed;
there is no global prune or deletion of shared application objects.

## Control correction, alternatives and review

The existing SEC-004 test mistakenly treated the new named-location Retry-After as
a server-level header and demanded it on static assets. It now collects actual
server-level headers at their configuration depth, retains all five security
requirements and exact asset-header comparison, and includes a seeded missing
X-Frame-Options counterexample. Named busy responses retain their own header checks
and native TLS evidence. No security assertion or budget was relaxed. Lead review
of this scope correction remains pending.

**Alternatives:** a per-IP origin limit does not bound shared upload work; API-only
limits miss origin buffering; removing body caps or draining rejected files
defeats admission. A fleet coordinator would require additional infrastructure and
operational qualification. Whole-handler cancellation risks interrupting command
commits. For registration, an outer rollback loses caller work; dialect-specific
upserts add separate write/metadata semantics. The scoped streaming/deadline and
savepoint approach preserves existing owners without a new dependency.

**Adversarial objection:** two origin slots trade throughput/fairness for bounded
admission, direct access bypasses that limit, and the observed memory margin is
small. Correct: this is one measured ingress profile. It cannot close fleet,
parallel worker, shared-disk or minimum-hardware acceptance. Savepoints add database
round trips for new metadata; required PG and native concurrency checks cover
transaction safety, not an unrestricted throughput claim.

**Rollback:** redeploy the earlier API/origin configuration together; no schema
migration or historical rewrite is needed. Reverting registry conflict handling
restores its race; preserve caller-owned command/receipt transactions and do not
remove stored objects to roll back metadata. No production deployment is performed.

**Lead decision: pending** on timeout/admission defaults, transaction ownership,
capacity headroom, configuration rollout and the SEC-004 control correction.
AI-assisted investigation, implementation, tests and documentation; no external
implementation copied.

**Subsequent repair:** [CAP-DISK-FULL](CAP-DISK-FULL-2026-10-18.md) qualifies bounded
local staging/object exhaustion and same-key browser recovery, with a scoped parser
capacity response. **Next:** parallel OCR/PDF and object-read budgets, shared disk
pressure across workers, supported replica/controller admission and deployment
headroom remain open.
Startup FX seed warnings and concurrent same-Message-ID delivery verification are
logged separately; no invented FX, retention or replay policy is introduced.

References consulted: [NGINX shared connection limits](https://nginx.org/en/docs/http/ngx_http_limit_conn_module.html),
[request buffering and HTTP version](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_request_buffering),
[body timeout and its 60-second default](https://nginx.org/en/docs/http/ngx_http_core_module.html#client_body_timeout).
