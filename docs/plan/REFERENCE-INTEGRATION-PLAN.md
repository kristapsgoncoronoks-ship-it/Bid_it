# Reference-integration plan — the eight-repository knowledge archive → InvoiceIQ

Received 2026-09-07: `InvoiceIQ_All_Reference_Investigations_20260907.zip` (archived
deduplicated at `docs/reference-investigations/`, index in `DUPLICATES.md`). Owner's order:
*"You must take ideas from all of these. Our system must include all of them in one
sophisticated service for clients … Involve all agents what are mentioned in files."*

This document is the single register for that order. It follows the archive's own rules:

- **Native implementation, not copying.** UNDERSTAND → EXTRACT PRINCIPLE → DESIGN → IMPLEMENT
  NATIVELY (`AI-ENGINEERING-POLICY.md`). Every archive patch was written against an older
  base (`d30f90b` / `584fb21` / `525470a`) and only one (`flask-route-topology-guard.patch`)
  still applies with `git apply --check`; the rest are re-implemented from their intent.
- **Status vocabulary** (`00-MASTER/README-FIRST.md`): PATCH READY → applied locally →
  targeted suites green → seeded violations red → full regressions green → CI green on the
  pushed head → **DONE** only then. Nothing in the archive was DONE on arrival (every cycle
  recorded `403 Resource not accessible by integration` on push).
- **Provenance fields** on every row: pattern ID · source repository · reference evidence ·
  InvoiceIQ evidence · Lead decision · validation status. Append only; PAT/NO IDs are kept.
- **Anti-cargo-cult**: a reference mechanism is evidence to learn from, not a default to copy;
  measurement before infrastructure; keep the repository's existing severity, never inflate.
- **Owner-only decisions** are recorded in `docs/DECISIONS-NEEDED.md`, never decided in code.

## 1. Roles the archive names — and how they are staffed here

| Role in the archive | Responsibility (from the files) | Staffed as |
|---|---|---|
| Lead Developer | final decision on every debate; implementation orders (objective / lesson / current / target / files / do-not-change / tests / acceptance / rollback); "APPROVE FOR BRANCH; NOT DONE" until CI | the implementing session (this plan's author); writes the verdict line per batch |
| Reference Repository Engineer (lens) | "principle transferred, not the reference's implementation" | post-implementation review agent per batch |
| InvoiceIQ System Architect | "same service/API/DB; no new subsystem" | post-implementation review agent per batch |
| Security Engineer | tenant/RLS/permission boundaries intact; "PASS WITH REQUIRED REGRESSION" when RLS tests must run | post-implementation review agent per batch; RLS suite on real Postgres |
| QA / Test Engineer | full repository tests, seeded-violation proofs, Postgres/RLS job tests | the pipeline discipline (targeted → seeded → full regression in a worktree → CI) |
| Adversarial Engineer | added complexity, hidden coupling, "never infer quality from the reference's popularity" | post-implementation review agent per batch |
| Product / UX | palette, jobs-operations screen, saved views: measured need first | review of R4 |
| DevOps / SRE | supply chain, workflow permissions, branch protection, deploy | R3 |
| Performance | PERF-DUCK-001 pushdown + re-measurement against datapoints 5–6 | R4 |
| Operators | scale lanes, override the OCR budget by environment, decide per-kind metrics | `OCR_PROCESS_TIMEOUT_SECONDS`, `--kinds` lanes (existing) |
| Owner / product owner | branch protection; the private disclosure channel; `STRIPE_METER_UPLOAD` go-live; payment/tax human approval policy | `DECISIONS-NEEDED.md` §20, §22, §23 |
| Human reviewer of AI-assisted change | can explain the change; no checkbox implies a test that was not run | PR template (R3), `AI-ENGINEERING-POLICY.md` |
| Future agents | append to the pattern library, never rediscover rejected patterns | `docs/reference-investigations/00-MASTER/` is the library; this register is the ledger |

The archive names no "SRE", "DevSecOps" or "Product Owner" verbatim (INVENTORY-learnings §Agents);
the table above maps its review lenses and category tags to the panel that reviews each batch.

## 2. Batches

Order follows the archive's dependency sequence (Stirling: "land heartbeat first; OCR timeout
second; full backend; Postgres/RLS job tests; full CI") and the patches inventory's
recommended application order.

| Batch | Scope | Status |
|---|---|---|
| **R1 — queue / runtime hardening** | STIR-P1-01 lease heartbeat · STIR-P2-01 OCR runtime budget + `processing_timeout` · PAT-004 Retry-After floor (capped) · PAT-028 connect-time DNS pin (CGNAT/NAT64, SNI, no redirects) · PAT-030 job log context · FLASK-P2-02 route-topology guard | **DONE — 2c631d5, CI #564 green, main fast-forwarded (deploy = CI #565)** |
| **R2 — billing durability** | BILL-REL-001 durable Stripe webhook (Job row, no new table; customer-mismatch terminal via `jobs.PermanentJobError`) · BILL-METER-001 frozen usage segment (`reporting_target`, migration `e7f9a1c3d5b8`) · BILL-QA-001 lost-response scenarios | **implemented; certification in §5b** |
| **R3 — security governance / supply chain** | SEC-SC-001 action SHA pins (+ `check_github_action_pins.py`, SHAs verified upstream) · SEC-SC-002/003 `security-supply-chain.yml` (actionlint, zizmor, TruffleHog, dependency-review; read-only token; `persist-credentials: false`) · explicit `permissions:` on every workflow · SEC-CTRL-001 control register + gate + generated view · ENG-GOV-001 PR template · FLASK-P2-01 JWT signing-key separation + rotation · SEC-GOV-001 disclosure decision doc (SECURITY.md stays BLOCKED on the owner) · `AI-ENGINEERING-POLICY.md` · TW-P2-02 AI-agent action safety as an ADR · TW-P2-03 migration law merged into `engineering-rules.md` §9 | queued |
| **R4 — performance / product** | PERF-DUCK-001 AP-inbox pushdown (one projected SELECT; no cache, no index) + PERF-DUCK-002 re-measure vs datapoints 5–6 (closes PERF-018) · TW-P2-01 command palette (filtered `navGroups` only; VR baseline reviewed) · FLASK-P3-01 warnings inventory then ratchet · Billing page "activating…" state after Checkout (R2 review P2) | queued |
| **Deferred / rule-only / rejected** | recorded in §4 with the archive's own decision; nothing silently dropped | — |

Per batch: targeted suites → seeded-violation proof for every control → full backend regression
in a git worktree → Postgres/RLS suite where jobs or tenant tables change → CI on the pushed head
→ review panel (§1) with a Lead verdict → `main` fast-forward (auto-deploys) → runbook rows.

## 3. Register — every order and code-bearing pattern

Legend: **Status** = DONE (CI green on main) · CERTIFYING (implemented, regressions running) ·
QUEUED (batch named) · OWNER (owner decision) · RULE (adopted as a rule, no code) ·
DEFERRED / REJECTED (archive decision kept).

### 3.1 Code and CI orders

| ID (archive) | Source repo · reference evidence | InvoiceIQ evidence (before) | Lead decision | Implementation (native) | Validation | Status |
|---|---|---|---|---|---|---|
| STIR-P1-01 (STIR-G1, STIR-D1, CMP-STIR-002) | Stirling-PDF · `ProcessExecutor` owns the resource for the operation's lifetime | `jobs.py`: `claim` stamps `locked_at`; nothing renewed it; `reclaim_stale` at 300 s handed a live 40-page OCR to a second worker | ADOPT C — P1, land first | `LEASE_HEARTBEAT_SECONDS = 60`; `_renew_lease` (own `SessionLocal`, predicate id + RUNNING + `locked_by == worker`); `_heartbeat_loop` on an `asyncio.Event`; task created in `run_once` after the tenant/job context is set, stopped and awaited in `finally`. The patches inventory's claim that "the heartbeat cannot fire during OCR because nothing offloads to a thread" was checked and is **refuted**: `extraction.py:435` and `email_intake.py:177` run the parse in `run_in_threadpool`, so the loop is free during OCR | 9 SQLite tests (`test_jobs.py`: renewal keeps a job live and out of `reclaim_stale`; a re-taken lease is not re-taken; a terminal job cannot be renewed; heartbeats while the handler is alive; a failing renewal does not fail the job; a HUNG renewal is cancelled after `HEARTBEAT_JOIN_SECONDS` (5 s) so the sequential worker is never held — review finding A-1; the task inherits tenant + job context; constants invariant 2×heartbeat ≤ stale, join < heartbeat), 2 Postgres/RLS tests (`test_stir_p1_lease_heartbeat_pg.py`: renewal on a second connection while the handler's transaction is open; renewal scoped by tenant under FORCE RLS — predicate also carries `org_id` explicitly, another tenant's scope renews nothing) — added to the CI postgres job (QA-001 gate); seeded: heartbeat task removed → red; tenant scope removed → red on Postgres; join unbounded → red | DONE (2c631d5, CI #564) |
| STIR-P2-01 (STIR-G2, STIR-004/005/020, STIR-D2) | Stirling-PDF · `processExecutor.timeoutMinutes` | three `pytesseract` calls with no `timeout=` (`pdf_ocr.py`, `receipt_ocr.py` — missed by the archive patch — and `extraction_provider.ImageProvider`) | ADAPT simple version — P2 | `Settings.ocr_process_timeout_seconds` (120, 0 < x ≤ 300); one bounded seam `pdf_ocr.ocr_image_to_data` used by both `image_to_data` sites; `OcrTimedOut`; `PROCESSING_TIMEOUT` in the closed capture vocabulary (`retry_helps=True`, `user_fixable=True`); providers map it; the three OCR routes answer 503 "OCR timed out" instead of 500 | 8 tests in `test_pdf_ocr.py` incl. the receipt path and all three routes (receipt scan, expenses statement import, reconciliation import); the routes answer the operator sentence `pdf_ocr.OCR_TIMED_OUT_DETAIL` ("Reading this file took longer than the server allows. Try again in a moment, or send a shorter or clearer scan.") and log the budget/page — review finding Product-3; the order named `tests/test_capture_failures.py`, which does not exist; `tests/test_capture_failure_worklist.py` is the equivalent and is green with the new kind; seeded: timeout dropped at either site → red; route mapping dropped → red | DONE (2c631d5, CI #564) |
| KT-001 / PAT-004 (REF-008, REF-013, CMP-005) | Scrapling · `throttle.py` AutoThrottle: a hint never speeds the crawler | `webhooks._http_post` discarded response headers; the queue alone chose 30 s | ADAPT NOW — P2 | `jobs.RetryAfterError(retry_after_seconds)`; `_fail(..., retry_after_seconds)` → `max(local_backoff, hint)`; `webhooks._parse_retry_after` (delay-seconds and HTTP-date; NaN/inf/garbage → None; past → 0); `deliver` raises `RetryAfterError` on a non-2xx with a parseable header | parametrised 5→30 / 120→120; **capped at `RETRY_AFTER_CAP_SECONDS` = 86 400** (review finding S-5: `Retry-After: 1e300` overflowed the datetime before commit and left a phantom RUNNING lease; `315360000` parked a delivery for a decade outside every gauge) — 1e300 / 315 360 000 / cap+1 → one day; non-finite or negative hint refused; last attempt still dead-letters; parser cases; end-to-end 503 + `Retry-After: 900` → `run_after = now + 900 s` (the Scrapling integration test the combined patch had dropped; the order's literal 429 case is covered by the status-agnostic path, not a separate test); seeded: floor removed → red, cap removed → red, header dropped → red | DONE (2c631d5, CI #564) |
| PNGX-P3-01 / PAT-028 (PNGX-009/010, TWENTY-017, SEC-SSRF-001, SEC-009) | Paperless-ngx · `network.py` connect-time validation; Twenty · connection-boundary SSRF | `assert_public_url` resolved before the request, httpx resolved again at connect (TOCTOU); fail-open on DNS error; no CGNAT/NAT64 exclusion | ADAPT NOW — keep P3 severity | `app/core/outbound_http.py`: `is_public_address` (explicit class checks — `is_global` alone lets ff02::1 through — plus 100.64.0.0/10 and 64:ff9b::/96), `resolve_hostname_ips` seam, `pin_request` (URL host → vetted IP, Host header and `sni_hostname` keep the name), `PinnedPublicAsyncHTTPTransport` resolving in `asyncio.to_thread` (blocking-IO rule); `webhooks._http_post` uses it with `follow_redirects=False`; `UnsafeOutboundUrl` at connect → terminal `blocked:` delivery, job succeeds; `_addr_is_public` delegates so registration and connect agree | `test_outbound_http.py` (18 classifications, Host/SNI/port, any-private-answer rejects, literal IP not resolved, IPv6 Host bracketed, NXDOMAIN is a ConnectError, transport refuses before a socket opens); `test_webhooks.py` proves the REAL `_http_post` goes through the pin, does not follow a redirect (one request, the 3xx is the outcome), and that registration and connect share one definition of public; seeded: transport bypassed → red; multicast let through → red; redirects followed → red; old registration definition → red. The explicit CGNAT/NAT64 list is NOT load-bearing on Python 3.11 — 100.64/10 is already `is_global == False`, and 64:ff9b::/96 (`is_global == True`!) is refused by the `is_reserved` class check — kept as version-drift insurance (production image is Python 3.14; classification there is unverified from this host — POSSIBLE). **Deliberate deviation from the order's "do not change registration-time semantics":** `_addr_is_public` now delegates to the same predicate, so registration refuses the wider set (CGNAT, 192.0.0.0/24, 198.18/15, …) — accepting at registration what delivery will block forever is worse | DONE (2c631d5, CI #564) |
| PNGX-P2-02 / PAT-030 (PNGX-007, PNGX-D6) | Paperless-ngx · `logging.py` ContextVar task id | worker log lines carried `request_id: "-"` and nothing else | ADAPT NOW — P2 | `job_id_ctx` / `job_kind_ctx` in `observability.py`, set/reset around dispatch, emitted by the JSON formatter only when set; inherited by the heartbeat task | context visible in the handler and reset after on the success AND failure paths; a real JSON log line inside a handler carries both keys; outside a job the keys are absent; documented for log pipelines in `docs/DEPLOYMENT.md` §5; seeded: context not set → red | DONE (2c631d5, CI #564) |
| FLASK-P2-02 (FLASK-001/023, FLASK-G2) | Flask · `setupmethod` refuses late registration; Blueprint duplicate checks | `router.py` relied on a comment to keep `/issued/recurring` ahead of `/issued/{invoice_id}` | ADOPT PRINCIPLE / IMPLEMENT NATIVELY — P2 | `tests/test_route_topology.py`: walks `app.routes`, reuses Starlette's compiled `path_regex`, zero allowlist; result on the live app: **no shadowed route** | self-test on a seeded fixture app names the pair; a correct-order fixture passes | DONE (2c631d5, CI #564) |
| BILL-REL-001 (LAGO-007/011/024, LAGO-D1/D2) | Lago · verified webhook → DB row → processing job | `routes/billing.py:stripe_webhook` applies inline and answers 200 on any exception (event lost) | ADOPT — P1; ADAPT: existing Job row, no new table; customer-mismatch is terminal | `billing.subscription_event_org_id` (side-effect free) → `enqueue_with_outcome(idempotency_key=event_id)` under the tenant guard → commit → 200 `{received, queued, created}`; enqueue failure → 503; handler `billing.apply_subscription_event` re-resolves the customer and raises the new `jobs.PermanentJobError` (dead-letter on attempt 1) on mismatch; ADR-0013 records the body contract change and the ordering assumption | 6 route/handler tests (`test_billing_stripe.py`) + 2 scenarios (`test_billing_reliability_scenarios.py`: worker dies mid-apply → reclaimed → applied once, ledger row once; triple redelivery → one job, one apply) + `PermanentJobError` test; RLS suite green on Postgres 16 with the change; seeded: enqueue failure answered 200 → red; permanent flag dropped → red; mismatch check removed → red | CERTIFYING (R2) |
| BILL-METER-001 (CMP-LAGO-002, LAGO-D3/D4) | Lago · immutable usage events by semantic id | `billing_usage.report_org_usage` recomputes delta and identifier from the live count — a lost response over-reports | ADOPT — P1 before meter activation | `UsageCounter.reporting_target` (+ migration `e7f9a1c3d5b8`, additive, backfill `= reported`, IMPACT/PREFLIGHT/NORMALIZATION/POSTCONDITION/ROLLBACK stated in the docstring per TW-P2-03); `SELECT … FOR UPDATE` on the counter; freeze + commit before the provider call; identifier from the target; on error `reported < reporting_target` so the same segment replays | lost-response scenario (count 10 → accepted, response lost → 15 → replay 10 under the same identifier → then 5 under a new one); commit-before-call proven by commit ordering (a second-session read cannot prove it on SQLite's shared connection); pre-migration row repaired; migration applied on local Postgres 16, DB-012 parity test green; seeded: commit → flush → red; identifier from live count → red; delta from live count → red. **Finding (pre-existing, not R2): `alembic check` on Postgres reports FK drift for the DB-018 column-list `SET NULL (project_id)` constraints, which SQLAlchemy renders as `SET NULL` — a comparison limitation of alembic's autogenerate, not a schema difference; the repository's own parity gate (`test_migrations.py`, DB-012) compares the real ON DELETE text and is green, and CI's `alembic check` step runs on SQLite where the rendering agrees. Recorded in FINDINGS as DB-019 (P4, informational)** | CERTIFYING (R2) |
| BILL-QA-001 (LAGO-016, LAGO-D7) | Lago · `spec/scenarios` | no lost-response scenarios | ADOPT | `tests/test_billing_reliability_scenarios.py` (webhook accepted → worker death → reclaim → applied once; redelivery ×3 → one job) + the meter lost-response / commit-order / repair scenarios in `test_billing_usage.py`; each asserts effects, identifiers, quantities and durable state | green | CERTIFYING (R2) |
| SEC-SC-001 / PNGX-P2-01 (PSC-007/008, PNGX-018, STIR-017, FLASK-020) | PSC/Paperless/Stirling/Flask · full-SHA pins | 25 mutable `uses:` tags across three workflows | ADOPT — P1 | pin to SHAs verified against upstream tags; `# vN` comments for Dependabot; `scripts/check_github_action_pins.py` in CI | pin script exits 0; CI green | QUEUED (R3) |
| SEC-SC-002 / SEC-SC-003 (PSC-010/011/012) | PSC · actionlint, zizmor, TruffleHog, dependency-review | pip-audit + PII scan only | ADOPT (hybrid) | `security-supply-chain.yml`, read-only token, `persist-credentials: false`; runs the pin check; non-required first | workflow green on the pushed head | QUEUED (R3) |
| PSC-009 / FLASK-019 | PSC/Flask · read-only default permissions | only `release.yml` has `permissions:` | ADOPT | explicit `permissions:` on every workflow | zizmor clean | QUEUED (R3) |
| SEC-CTRL-001 (PSC-001/002/004/018/021) | PSC · YAML source → generated checklist | no compact register | ADOPT C | `docs/security/security-controls.json` + `scripts/security_control_gate.py --check/--render` + generated `SECURITY-CONTROLS.md`; statuses updated as R1–R4 land (SEC-SSRF-001 → verified with `tests/test_outbound_http.py`) | gate in CI | QUEUED (R3) |
| ENG-GOV-001 (PSC-005/006/019) | PSC · evidence-first PR template | none | ADOPT | `.github/PULL_REQUEST_TEMPLATE.md` incl. AI-assistance disclosure | — | QUEUED (R3) |
| FLASK-P2-01 (FLASK-010, FLASK-G1, FLASK-D1) | Flask · `SECRET_KEY_FALLBACKS` | access tokens and OIDC state sign with `secret_key`, which also derives the local KEK | ADAPT C — P2 | `jwt_signing_key` + ≤3 verify-only fallbacks; sign current, verify current then fallbacks; production refuses insecure/duplicate keys; KEK derivation untouched; jti/session gate stays authoritative | rotation keeps revocation authoritative; OIDC state survives staged rotation, dies when the fallback is retired | QUEUED (R3) |
| PERF-DUCK-001 / PERF-DUCK-002 (DUCK-001/002/003/021, DUCK-D1/D5) | DuckDB · filter/projection pushdown | `approval_policy.waiting_for` hydrates every pending step + invoice + vendor and reduces in Python (PERF-018) | ADAPT NOW — P2; no cache, no index in the same change | one projected SELECT with current-step, SoD and assignee predicates and `LIMIT` in SQL | one-SELECT execution-shape test; re-measure concurrency vs datapoints 5–6 | QUEUED (R4) |
| TW-P2-01 (TWENTY-005, TWENTY-D3) | Twenty · permission-aware command menu | no palette; `Layout.filterNav` is the one filter | ADAPT NOW — P2 | `CommandPalette.tsx` over the filtered `navGroups`; Ctrl/⌘+K; no API; `quickNavigation` prop off in the design showcase | owner reaches `/invoices`; a role without Upload cannot find it; VR baseline reviewed | QUEUED (R4) |
| FLASK-P3-01 (FLASK-013, FLASK-D3) | Flask · `filterwarnings = error` | `pytest.ini` ignores every DeprecationWarning | ADAPT, do not flip blindly | inventory run with `-W default::DeprecationWarning`, classify, exact filters, then `error` | inventory file committed under `docs/perf/` | QUEUED (R4) |

### 3.2 Documents and policies

| ID | Source | Decision | Placement | Status |
|---|---|---|---|---|
| KT-002 `AI-ENGINEERING-POLICY.md` | Scrapling `AI_POLICY.md` | ADOPT | `docs/architecture/AI-ENGINEERING-POLICY.md`, linked from `engineering-rules.md` | QUEUED (R3) |
| TW-P2-02 AI-agent action safety (11 conditions) | Twenty agent roles | ADOPT AS ARCHITECTURE LAW; no write agent until then | ADR under `docs/architecture/adr/`, cross-linked from ADR-0027 | QUEUED (R3) |
| TW-P2-03 migration law | Twenty plan/apply | ADAPT as policy, reject engine | merged into `engineering-rules.md` §9 (avoid two rulebooks) | QUEUED (R3) |
| SEC-GOV-001 disclosure decision | PSC | BLOCKED on owner | `docs/security/VULNERABILITY-DISCLOSURE-DECISION.md` + `DECISIONS-NEEDED.md` §22; `SECURITY.md` unchanged until the channel exists and is tested | QUEUED (R3) / OWNER |
| KT-003 pattern library | Scrapling | ADOPT | `docs/reference-investigations/00-MASTER/` is the library; append-only | DONE (archived this commit) |

### 3.3 Owner decisions (never decided in code)

| Item | Where |
|---|---|
| Branch protection on `main` (required checks pii-scan, lint, backend, postgres, frontend, frontend-e2e, docker-build; not deploy; block force-push/delete; no second approval while solo) — PNGX-P1-01 / OPS-GOV-001, P1 | `DECISIONS-NEEDED.md` §20 (already listed) and §23 |
| Private vulnerability-disclosure channel (GitHub PVR preferred, or a monitored mailbox) — SEC-GOV-001, P1 | `DECISIONS-NEEDED.md` §22 |
| `STRIPE_METER_UPLOAD` go-live (BILL-METER-001 is a hard precondition) | `DECISIONS-NEEDED.md` §18 (billing) |
| GitHub integration write permission (the archive's 403) — not needed here; this session pushes | none |

## 4. Rule-only, deferred and rejected patterns (archive decisions kept)

Adopted as rules (no code): S3 idempotency identity from semantic inputs; S9 bound task creation
at the first real fan-out; P11/P12 secondary indexes enforce tenant filters and stay rebuildable;
D3 batch processing; D17 performance rewrites never bypass RLS; T4 one migration writer;
T23 documented dependency overrides; F14 fail-closed security capabilities; STIR-020 resource
policy lives in `Settings`; LAGO-018 NOT VALID deadline; L20/D16 agent contracts link to
`engineering-rules.md`.

Deferred with a trigger: KT-004 extraction replay corpus (P2, after PII quarantine design);
KT-005 AP confirmed-output drift (design after data-grain review); KT-006–011, PNGX-P2-03 jobs
operations screen (product decision), PNGX-P3-02/03/04, PNGX-P4-01/02, TW-P3-01/02/03,
TW-P4-01/02, PSC CI-PATH-001, JOB-OBS-001, DB-MIG-VALIDATE-001, PERF-DUCK-003, QA-DUCK-001,
SEC-DUCK-001, STIR-P3-01, STIR-P4-01/02/03, FLASK-P3-02, FLASK-P4-01/03.

Rejected (do not re-propose): Scrapling REJECT-001..010; Paperless NOT-adopt 1–10; Twenty
NOT-adopt 1–12; PSC NOT-adopt 1–12; Lago NOT-adopt 1–15; DuckDB NOT-copy 1–15;
Stirling NOT-adopt 1–15; Flask NOT-copy 1–15 (full lists in the cumulative learnings file).

## 5. Batch R1 — certification record

| Step | Result |
|---|---|
| Targeted suites (after the review fixes) | `test_jobs.py` 37 · `test_webhooks.py` 16 · `test_outbound_http.py` 27 · `test_route_topology.py` 3 · `test_pdf_ocr.py` 10 · capture worklist, bank statement, AR legal, OpenAPI truth, QA-001 gate — **126 passed / 0 failed** in one run; blocking-IO gate, automation webhooks green earlier |
| Postgres / RLS (local Postgres 16, `RLS_TEST_DATABASE_URL`) | heartbeat PG tests 2 / 0 (re-run after the `org_id` predicate) with BE-003 and RLS connection reuse (5 / 0); ARCH-013 advisory lock green in the first round |
| Seeded violations | 14 of 15 caught: heartbeat removed, tenant scope removed (Postgres), join unbounded, Retry-After floor removed, cap removed, header dropped, raise dropped, log context not set, transport bypassed, redirects followed, old registration definition, multicast let through, OCR timeout dropped ×2, statement-route 503 dropped. Not load-bearing: the explicit CGNAT/NAT64 list (see the PAT-028 row) — recorded, not claimed as a control |
| ruff / mypy | clean (393 files) |
| Full backend regression (worktree, final tree after the review fixes) | **3198 passed / 19 skipped / 0 failed (54:35)** — 3217 collected |
| CI | **#564 at 2c631d5 SUCCESS, all seven active jobs** (19:52–20:16 UTC; backend 23:54; postgres job ran the two heartbeat PG tests; frontend-e2e 3:42; deploy skipped on the branch). `main` fast-forwarded to 2c631d5 → **CI #565 (push) SUCCESS, all nine jobs incl. deploy — production 2c631d5 since 21:02 UTC** (backend Healthy 16 s after start; no migration) |
| Review panel | recorded in §6 after the run |

## 5b. Batch R2 — certification record

| Step | Result |
|---|---|
| Targeted suites (after the review fixes) | `test_billing_stripe.py` 22 · `test_billing_usage.py` 14 · `test_billing_reliability_scenarios.py` 5 · `test_jobs.py` 38 · `test_migrations.py` (DB-012 parity) · `test_docs_truth.py` (README 132 revisions) · `test_openapi_truth.py` · `test_wo_ad_retention_ladder.py` — all green (104 in the first round, 47 + 9 after the fixes) |
| Postgres (local 16, `RLS_TEST_DATABASE_URL`) | migration `e7f9a1c3d5b8` applied; `test_bill_meter_segment_pg.py` (a second connection reads the committed segment and takes `FOR UPDATE NOWAIT` during the provider call on the freeze AND replay paths), `test_rls.py`, heartbeat, BE-003, usage-counter concurrency — 9 / 0 on a quiet database (one earlier `test_rls.py` failure was residue from a killed concurrent run: a leftover `switched@x.io` user; removed) |
| Seeded violations | 9 of 9 caught: enqueue failure answered 200; permanent flag dropped; mismatch check removed; supersession check removed; empty event id accepted; segment flushed not committed; identifier from live count; delta from live count; post-ack advance made a blind write |
| ruff / mypy | clean (393 files) |
| `alembic check` | green on SQLite (CI's mode); on Postgres it reports the pre-existing DB-018 column-list rendering difference — DB-019, informational |
| Full backend regression (worktree, final tree) | **3213 passed / 20 skipped / 0 failed (54:56)** — 3233 collected |
| CI | recorded in the dashboard when read |

## 6. Review panel verdicts

Filled per batch after implementation (Reference lens · Architect · Security · QA · Adversarial ·
Product/UX · DevOps · Performance · Lead).

### R1 — panel run 2026-09-07 on the uncommitted tree (three review agents, read-only), fixes applied before certification

| Lens | Verdict | Findings acted on |
|---|---|---|
| Reference Repository Engineer | PASS WITH REQUIRED FOLLOW-UP → follow-ups done | provenance text corrected (NAT64 refused by `is_reserved`, not `is_global`); the widened registration-time address set named as a deliberate deviation; added tests: terminal job cannot be renewed, context reset on the failure path; `test_capture_failures.py` → `test_capture_failure_worklist.py` substitution recorded |
| InvoiceIQ System Architect | PASS | no new table, dependency, process or parallel abstraction; layering core ← services intact; heartbeat uses the existing `SessionLocal` per renewal; OCR budget in `Settings`. Noted, not acted on: the timeout classifier exists twice (`pdf_ocr.ocr_image_to_data`, `ImageProvider`), `webhooks.py`'s lazy `import httpx` now redundant |
| Security Engineer | PASS WITH REQUIRED FOLLOW-UP → fixed | **S-5 CONFIRMED** unbounded Retry-After (overflow → phantom RUNNING lease; decade-long park) → `RETRY_AFTER_CAP_SECONDS` + non-finite refused + 3 tests; S-1 hardening `Job.org_id` in the renewal predicate; S-2 classification probed on 3.11/3.12/3.13 (all listed classes refused; `::ffff:8.8.8.8` and 192.0.0.9/.10 accepted by IANA design); S-8 POSSIBLE: production image is Python 3.14, classification unverified there |
| Adversarial Engineer | PASS WITH REQUIRED FOLLOW-UP → fixed | **A-1 CONFIRMED** `await heartbeat_task` unbounded on a hung renewal → `HEARTBEAT_JOIN_SECONDS` = 5 s then cancel, test with a renewal that never returns; A-5 claims not test-enforced → tests for `follow_redirects=False`, registration/connect agreement, the two statement routes' 503, and the constants invariant; A-6 vacuous sleeps removed; A-7 benign completion race now logged at info with a neutral message; A-4 `HTTPS_PROXY` bypass documented in `DEPLOYMENT.md` |
| QA / Test Engineer | PASS WITH REQUIRED FOLLOW-UP → fixed | the two untested 503 branches now tested; seeded-violation record judged credible for all nine original controls; flake probe 5/5 green on the heartbeat tests; count bookkeeping corrected in §5 |
| Performance | PASS | ≤ 1 heartbeat per process (sequential worker), one UPDATE/min only for jobs over a minute; `to_thread(getaddrinfo)` cost-neutral (httpx resolved in a thread anyway, now connects to a literal); no perf-harness path touched; POSSIBLE: httpx now in the API startup import graph (startup-heap gate still green) |
| DevOps / SRE | PASS WITH REQUIRED FOLLOW-UP → fixed | `DEPLOYMENT.md`: `job_id`/`job_kind` log fields, two connections per worker in the connections alert, worker grace ≥ OCR budget, 503-in-5xx-rate note, proxy note; SIGTERM path confirmed (in-flight job completes with the lease renewed); CI postgres list correct |
| Product / UX | PASS WITH REQUIRED FOLLOW-UP → fixed | the 503 detail was stack vocabulary ("OCR timed out: OCR exceeded 120 seconds") shown verbatim by three screens → one operator sentence, exception text to the log; remediation no longer promises "this reference" the card does not show; worklist prose confirmed operator-grade and Retry offered (`retry_helps=True`) |
| **Lead Developer** | **DONE** — full regression 3198 / 19 / 0, CI #564 green on 2c631d5, main fast-forwarded | |

### R2 — panel run 2026-09-07 on the uncommitted tree (two review agents, eight lenses), fixes applied before certification

| Lens | Verdict | Findings acted on |
|---|---|---|
| Reference Repository Engineer | PASS WITH REQUIRED FOLLOW-UP → done | **R-1 LIKELY** durable retry re-orders events for one subscription (a failed `updated{active,pro}` retried after `deleted{canceled}` applied) → a job is SUPERSEDED when a newer job for the same subscription already succeeded (queue-table lookup; the reducer stays untouched per the order's do-not-change list) + scenario test; R-4 "ack then local commit fails" scenario added; adaptations A (terminal mismatch) and B (`applied` dropped) judged correct |
| InvoiceIQ System Architect | PASS | `PermanentJobError` is the right seam (an outcome property, not a kind property; precedent `RetryAfterError`); migration additive, chain linear, `server_default` matches the model; mid-loop commit does not disturb `_complete`/`_fail` (`expire_on_commit=False`); A-3 rollback precondition written into the migration docstring; A-5 comment on the repair branch |
| Security Engineer | PASS | a signed event cannot reach an arbitrary tenant (`stripe_customer_id` is unique and written only from our own `Customer.create`); enqueue under the tenant guard passes FORCE RLS (probed on Postgres); 503 body constant; **S-4** a verified body without an `id` (reachable only with the signing secret) would have made a job per delivery → refused as a harmless 200, test added; a dead-letter's `last_error` shows the tenant its own former customer id only |
| Adversarial Engineer | PASS WITH REQUIRED FOLLOW-UP → done | **A1 CONFIRMED (reproduced)** the single-worker backoff case of R-1 → fixed by the supersession check (the probe scenario is now the committed test `test_a_stale_retry_does_not_undo_a_newer_event_for_the_same_subscription`); **A3** the post-ack advance was a blind write → compare-and-set (`UPDATE … WHERE reporting_target = target AND reported < target`), test; A2 worker down for days → `/health/queue` documented as a REQUIRED uptime check; A4/A5 verified safe (mid-loop commit vs rollback; RLS GUC at the INSERT) |
| QA / Test Engineer | PASS WITH REQUIRED FOLLOW-UP → done | tests assert state, not call counts; the sibling-helper import has precedent; the commit-order proof got its Postgres twin (`test_bill_meter_segment_pg.py`, in the CI postgres list, QA-001 gate green); Q4 two-events-one-customer gap closed by the A1 test; README 132 revisions verified |
| Performance | PASS WITH REQUIRED FOLLOW-UP → done | **P1 CONFIRMED** the replay path held `FOR UPDATE` across the Stripe call (up to 80 s), stalling that tenant's upload counter (`access.record_usage` writes the same row) → the commit now runs before EVERY provider call; proven by `FOR UPDATE NOWAIT` from a second connection on Postgres; webhook route cost neutral; perf-harness endpoints untouched |
| DevOps / SRE | PASS WITH REQUIRED FOLLOW-UP → partly done | worker lanes absorb the new kind (D1); migration/deploy ordering safe, old code on the new column handled by the repair branch (D2); D3 `/health/queue` documented as required (done); a `kind` label on the dead-letter gauge deferred to the ops group (it changes the Prometheus series shape; recorded below) |
| Product / UX | PASS WITH REQUIRED FOLLOW-UP → partly done | no `applied` consumer; P3 the three stale doc sentences (data-flows, architecture/deployment, overview) rewritten; **P2 open → R4:** after Checkout the Billing page shows the old plan until the worker applies the event and the "Subscribe" button stays enabled — add an "activating…" state with a refetch until the plan changes (recorded in ADR-0013 and §2 R4) |
| **Lead Developer** | **APPROVE FOR BRANCH — DONE when the full regression and CI on the pushed head read green** | |

Deferred from the R2 review, with owners: `kind` label on the dead-letter gauge and `/health/queue` in the compose/k8s probes (ops group, with OPS-003/007/009/011/013); Billing page "activating…" state (R4 product).
