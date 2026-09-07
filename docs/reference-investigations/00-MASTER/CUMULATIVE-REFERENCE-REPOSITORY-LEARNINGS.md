# Reference Repository Learnings — D4Vinci/Scrapling

**Reference repository:** D4Vinci/Scrapling  
**Reference commit studied:** `28c329671485daaea89a40fb34a7db8622e51468`  
**Our repository:** `kristapsgoncoronoks-ship-it/Bid_it`  
**Our base commit:** `d30f90b69566b4d9055e597bd824932e25e4daf6`  
**Analysis date:** 2026-09-06  
**Purpose:** Transfer engineering principles into InvoiceIQ/Bid_it without copying architecture blindly.

---

## 1. Repository purpose

Scrapling is an adaptive Python web-scraping framework. It spans three execution levels:

1. fast/static HTTP fetching;
2. dynamic browser fetching;
3. stealth/browser execution for protected sites.

Above those transports it provides:

- a common parsing/selector model;
- adaptive element relocation after page structure changes;
- an asynchronous spider framework;
- priority scheduling and request deduplication;
- per-domain/global concurrency controls;
- automatic throttling and `Retry-After` handling;
- multiple named sessions;
- pause/resume via checkpoints;
- development response replay;
- bounded streaming;
- crawl statistics;
- CLI and AI/agent-facing documentation.

It is not a multi-tenant SaaS, financial ledger, workflow engine, or agent graph. Its best lessons for InvoiceIQ therefore come from orchestration, backpressure, replayability, adapter ergonomics, testing, and AI-development governance.

---

## 2. Architecture map

```text
User / Python / CLI / Agent skill
              |
              v
        Public Fetcher API
              |
   +----------+-----------+
   |                      |
   v                      v
Static HTTP        Browser / stealth
engines             engines
   |                      |
   +----------+-----------+
              |
              v
            Response
              |
              v
          Parser / Selector
              |
              +--> adaptive element storage/relocation
              |
              v
        extracted values

For larger crawls:

Spider
  |
  v
Scheduler -- fingerprint/dedup/priority
  |
  v
CrawlerEngine
  |
  +--> global/domain capacity limits
  +--> AutoThrottle
  +--> robots/delay policy
  +--> development response cache
  +--> checkpoint manager
  |
  v
SessionManager
  |
  +--> static session
  +--> dynamic session
  +--> stealth session
  |
  v
Response -> callback -> Item or more Requests
                   |
                   v
            bounded item stream
```

### Why this shape works

The public API remains stable while transport complexity varies. Scheduling does not know browser internals; parsing does not own concurrency; checkpoints do not own business callbacks; named sessions isolate execution strategies; the spider supplies domain-specific behavior while the engine supplies orchestration.

---

## 3. Runtime execution flow

```text
start_urls / start_requests()
        |
        v
Request(url, callback, session, priority, metadata)
        |
        v
canonical fingerprint
        |
        +--> already seen? drop unless explicitly bypassed
        |
        v
priority scheduler
        |
        v
bounded task creation
        |
        v
global + per-domain capacity
        |
        v
configured delay / AutoThrottle
        |
        v
SessionManager selects execution strategy
        |
        v
network/browser fetch
        |
        +--> development cache hit? replay instead
        |
        v
Response
        |
        +--> blocked? retry with lower priority/backoff
        |
        v
callback
        |
        +--> Item -> output / stream
        +--> Request -> scheduler
        |
        v
complete request + periodic atomic checkpoint
```

---

# 4. Top engineering ideas

## REF-001 — Stable façade over interchangeable execution strategies

**Category:** Architecture  
**Evidence:** `scrapling/fetchers/`, `scrapling/engines/`, `scrapling/spiders/session.py`

A small public interface sits above static HTTP, browser, and stealth implementations.

**Problem solved:** consumers do not carry transport-specific branching everywhere.

**Advantages:** stable API, extension seam, centralized lifecycle, lazy expensive strategies.

**Disadvantages:** too-wide common interfaces can hide meaningful capability differences.

**Transferability:** **MEDIUM — already largely present in InvoiceIQ.**

InvoiceIQ already has `ExtractionProvider`, `BillingProvider`, storage backends, fuel-card parser registry, ERP exporter seams, email adapters and SSO connections.

**Lead decision:** **KEEP OUR APPROACH.** Do not create a new central `IntegrationManager`.

---

## REF-002 — Typed work request as orchestration unit

**Category:** Backend / Architecture  
**Evidence:** `scrapling/spiders/request.py`

A request carries URL, callback, priority, session identity, metadata, retry count and execution kwargs.

**Principle:** the unit of work should carry the data required to identify, route and execute it.

**Transferability:** **MEDIUM.**

InvoiceIQ's durable `Job` row is the equivalent, with kind, payload, tenant, idempotency key, attempts, lease and schedule.

**Decision:** **KEEP the DB job model.** If needed, add typed validation per job kind rather than an in-memory request abstraction.

---

## REF-003 — Canonical work fingerprinting

**Category:** Reliability  
**Evidence:** `scrapling/spiders/request.py`

Scrapling derives identity from canonical URL + method + body + session identity, with optional material inputs.

**Principle:** idempotency identity should come from semantically meaningful inputs, not incidental row IDs or timestamps.

**Transferability:** **HIGH as a design rule; LOW as copied code.**

InvoiceIQ already uses DB-enforced idempotency keys.

**Decision:** **ADAPT as an engineering rule.** Database uniqueness remains authoritative.

---

## REF-004 — Recovery includes in-flight work

**Category:** Reliability  
**Evidence:** `scrapling/spiders/scheduler.py`

The scheduler snapshot includes queued and dequeued-but-not-completed work.

**Principle:** process death must not create an invisible gap.

**Transferability:** **HIGH principle / no change required.**

InvoiceIQ already does this more safely through durable jobs, leases and `reclaim_stale`.

**Decision:** **NO CHANGE REQUIRED.**

---

## REF-005 — Atomic checkpoint/cache writes

**Category:** Reliability / DevEx  
**Evidence:** `scrapling/spiders/checkpoint.py`, `scrapling/spiders/cache.py`

Temporary-file writes are atomically replaced into the final path.

**Principle:** recoverable developer state should not become half-written state.

**Transferability:** **MEDIUM.**

Use for local generated artifacts/replay fixtures. Production finance state stays in PostgreSQL/object storage.

**Decision:** **ADAPT only for filesystem tooling.**

---

## REF-006 — Development response replay

**Category:** Testing / Developer experience  
**Evidence:** `scrapling/spiders/cache.py`

Responses can be persisted by fingerprint and replayed while parser logic changes.

**Problem solved:** parser development becomes reproducible without repeatedly hitting an expensive or unstable external source.

**Transferability:** **HIGH.**

### InvoiceIQ adaptation

Build a **test-only sanitized extraction replay corpus**, not a production HTTP cache.

Suggested shape:

```text
backend/tests/fixtures/extraction_replay/
  manifest.json
  <case-id>/
    source.<pdf|xml|csv|json>
    expected.json
```

Expected output should record provider/method, normalized headers, lines, provenance class and warnings.

**Decision:** **ADAPT — P2.**

---

## REF-007 — Adaptive memory after structural drift

**Category:** Parsing / Reliability  
**Evidence:** `scrapling/parser.py`, `scrapling/core/storage.py`

Scrapling stores properties of a known element and later scores candidates when the selector no longer finds it.

**Principle:** retain evidence of known-good interpretation and detect drift.

**Transferability:** **HIGH principle, LOW direct algorithm.**

### InvoiceIQ already learned the safer version

`backend/app/services/transport/extraction_baseline.py` stores known-good statement aggregates, re-parses later, flags drift, never changes financial data automatically, and requires an explicit audited human rebaseline.

**Decision:** **KEEP transport implementation.**

For AP invoices, consider confirmed-output drift detection, but **never fuzzy-auto-correct money, bank details or supplier identity.**

---

## REF-008 — Closed-loop backpressure and `Retry-After`

**Category:** Performance / Reliability  
**Evidence:** `scrapling/spiders/throttle.py`

Scrapling observes latency, keeps per-domain delay, reacts to blocked/error responses and honors `Retry-After`. Error responses cannot make the crawler faster.

**Underlying principle:** incorporate explicit downstream feedback into retry/backpressure policy.

**Transferability:** **HIGH for outbound providers.**

### Concrete InvoiceIQ problem

`backend/app/services/webhooks.py` currently discards response headers. Every non-2xx becomes a generic failure and the durable queue alone chooses 30s/60s/... backoff even if the receiver explicitly says when to retry.

**Decision:** **ADAPT NOW.**

The prepared patch adds a generic queue retry-delay hint and consumes webhook `Retry-After` while preserving the DB queue.

---

## REF-009 — Bound task creation, not only resource acquisition

**Category:** Performance  
**Evidence:** `scrapling/spiders/engine.py`

The crawler does not spawn unlimited tasks that merely wait on a semaphore; active task creation itself is bounded.

**Principle:** backpressure begins at the producer.

**Transferability:** **MEDIUM/HIGH.**

InvoiceIQ's main worker is sequential per process and already has workload lanes, so there is no equivalent task explosion in that path.

**Decision:** **KEEP current worker semantics.** Use this as a rule for future batch fan-out/provider calls.

---

## REF-010 — Named strategy registry with lazy lifecycle

**Category:** Architecture  
**Evidence:** `scrapling/spiders/session.py`

Named sessions have explicit lifecycle and optional lazy start.

**Principle:** expensive adapters should be initialized only when required.

**Transferability:** **MEDIUM.**

InvoiceIQ already has context-owned provider seams.

**Decision:** **REJECT a global lifecycle registry.** Add lazy lifecycle inside a specific adapter if a real provider requires it.

---

## REF-011 — Rich execution statistics

**Category:** Observability  
**Evidence:** `scrapling/spiders/result.py`

Scrapling reports request counts, failures, statuses, bytes, cache hits, session distribution, blocked requests and throttle state.

**Principle:** long-running orchestration should expose workload shape, not just completion.

**Transferability:** **MEDIUM.**

InvoiceIQ already has operationally stronger queue SLO metrics: status counts, dead-letter depth and oldest-ready-job age.

**Decision:** **KEEP existing SLOs.** P3 only: per-job-kind duration/attempt histograms if operations needs them.

---

## REF-012 — Optional capability bundles and lazy imports

**Category:** Packaging  
**Evidence:** `pyproject.toml`, `scrapling/fetchers/__init__.py`

Browser/AI/RAG capabilities are optional extras and fetcher imports are lazy.

**Principle:** do not make every deployment pay for unused heavyweight capabilities.

**Transferability:** **LOW/MEDIUM for a controlled SaaS app.**

**Decision:** **DEFER.** Revisit if OCR/AI workers need materially different system packages/images.

---

## REF-013 — Behavioral invariant testing

**Category:** Testing  
**Evidence:** `tests/spiders/test_scheduler.py`, `tests/spiders/test_throttle.py`

Tests prove duplicate suppression, FIFO, recovery of in-flight work, independent domain state, Retry-After behavior and invalid configuration.

**Principle:** test failure semantics/invariants, not only happy outputs.

**Transferability:** **HIGH.**

InvoiceIQ already follows this strongly for RLS, money, idempotency, concurrency and migrations.

**Decision:** **KEEP AND EXTEND.** New retry tests assert that external hints can lengthen but never shorten queue backoff.

---

## REF-014 — Complete CI diagnostic fan-in

**Category:** CI / Developer experience  
**Evidence:** `.github/workflows/code-quality.yml`

Independent checks finish, a summary reports every result, then the workflow fails once.

**Principle:** one CI run should ideally reveal all independent static failures.

**Transferability:** **MEDIUM.**

InvoiceIQ already has well-separated CI jobs, but its lint job is sequential.

**Decision:** **P3 ADAPT only if feedback latency becomes a real problem.**

---

## REF-015 — Source-security scanning separate from dependency scanning

**Category:** Security  
**Evidence:** Scrapling uses Bandit; InvoiceIQ uses `pip-audit` plus custom security/structural tests.

**Principle:** source security scanning and dependency-CVE scanning solve different classes of problems.

**Transferability:** **MEDIUM.**

**Decision:** **DEFER tool choice.** First compare Bandit with a curated Ruff `S` ruleset and measure signal/false positives.

---

## REF-016 — AI contribution provenance policy

**Category:** Engineering governance  
**Evidence:** `AI_POLICY.md`

Scrapling requires disclosure of AI assistance, human understanding, rationale and concrete evidence.

**Transferability:** **VERY HIGH.**

InvoiceIQ is heavily agent-assisted and commit history already contains session metadata, but no equivalent repository development policy was found.

**Decision:** **ADOPT/ADAPT NOW.** Proposed `docs/AI-ENGINEERING-POLICY.md` is included in the transfer bundle.

---

## REF-017 — Agent-facing operating contract

**Category:** Agent engineering  
**Evidence:** `agent-skill/Scrapling-Skill/SKILL.md`

Scrapling packages prerequisites, escalation strategy, security warnings and examples for AI operators. It specifically warns about prompt injection when content is fed to AI.

**Principle:** machine operators need a reviewed operating contract just like human contributors.

**Transferability:** **MEDIUM/HIGH for future InvoiceIQ agent tooling.**

**Decision:** **P3 FUTURE.** When InvoiceIQ exposes agent tools, define tenant/permission boundaries, no-invented-financial-facts rules, prompt-injection treatment and confirmation requirements.

---

## REF-018 — Escalate from cheap/deterministic to expensive/uncertain

**Category:** Architecture / Performance  
**Evidence:** Scrapling guidance favors static fetch → dynamic fetch → stealth only as needed.

**Principle:** start with the simplest deterministic path and escalate only when evidence says it failed.

**Transferability:** **HIGH and already aligned.**

InvoiceIQ already uses deterministic-first extraction and CI-proves that default operation makes zero external AI calls.

**Decision:** **KEEP.**

---

# 5. Patterns we should not copy

## REJECT-001 — Replace PostgreSQL jobs with Scrapling scheduler
InvoiceIQ's durable DB queue already provides idempotency, atomic claim, tenant scope, leases, stale-worker recovery, backoff and dead letters.

**REJECT.**

## REJECT-002 — Pickle production workflow state
Appropriate for trusted local crawler recovery; inappropriate as authoritative financial state.

**REJECT.**

## REJECT-003 — Fuzzy automatic financial-field relocation
Wrong-but-plausible money/IBAN/supplier data is more dangerous than a visible parse failure.

**REJECT automatic mutation.**

## REJECT-004 — Browser stealth/anti-bot stack
No core InvoiceIQ problem requires it.

**REJECT.**

## REJECT-005 — Spider inheritance as business workflow model
Would hide transactions/permissions behind callbacks and duplicate existing service/job architecture.

**REJECT.**

## REJECT-006 — Universal IntegrationManager
Context-owned adapters are clearer and safer.

**REJECT.**

## REJECT-007 — Broad Python-version CI matrix
Scrapling is a distributed library; InvoiceIQ owns its runtime.

**REJECT until portability is a requirement.**

## REJECT-008 — Duplicate type checker because the reference has one
Do not add Pyright just because Scrapling also runs mypy.

**REJECT absent measured value.**

## REJECT-009 — Generic filesystem response cache in production
Replay belongs in development/tests; production inputs remain durable and audited.

**REJECT production cache.**

## REJECT-010 — Increase worker concurrency because Scrapling is concurrent
InvoiceIQ already shows PostgreSQL contention in measured concurrent aggregate reads. More concurrency is not automatically more throughput.

**REJECT without workload evidence.**

---

# 6. Where InvoiceIQ is already better for its use case

1. PostgreSQL-backed durable jobs rather than process scheduler + checkpoint file.
2. Request scope + ORM guard + PostgreSQL FORCE RLS.
3. Deny-by-default capability authorization with route-coverage enforcement.
4. Decimal/Numeric money and currency-provenance invariants.
5. Hash-chained, tenant-sequenced audit trail.
6. Alembic single-head/drift checks and real PostgreSQL CI.
7. Payment/credit/numbering/claim-lock/usage-counter concurrency tests.
8. Default external-AI network denial enforced by CI.
9. Queue lag/dead-letter SLO health and Prometheus metrics.
10. Advisory audited anti-drift for transport extraction rather than fuzzy self-correction.

---

# 7. Structured comparison

| ID | Reference approach | InvoiceIQ approach | Recommendation | Confidence |
|---|---|---|---|---|
| CMP-001 | process priority scheduler | durable Postgres queue | KEEP ours | HIGH |
| CMP-002 | pickle pause/resume | leases + stale reclaim | KEEP ours | HIGH |
| CMP-003 | response replay cache | source docs + normal fixtures | ADAPT test replay | HIGH |
| CMP-004 | fuzzy adaptive relocation | transport anti-drift baseline | KEEP safer finance version | HIGH |
| CMP-005 | AutoThrottle + Retry-After | fixed queue backoff | ADAPT Retry-After | HIGH |
| CMP-006 | SessionManager registry | context-owned provider registries | KEEP ours | HIGH |
| CMP-007 | bounded task creation | sequential worker + lanes | KEEP; apply invariant to future fan-out | HIGH |
| CMP-008 | crawl run stats | queue SLO + Prometheus | KEEP; P3 per-kind metrics | MEDIUM |
| CMP-009 | optional extras | controlled server image | DEFER | HIGH |
| CMP-010 | Bandit + two type checkers | Ruff + mypy + pip-audit + structural tests | DEFER tool change | HIGH |
| CMP-011 | AI contribution policy | runtime AI policy, no dev policy found | ADOPT governance | HIGH |
| CMP-012 | agent skill | no product-agent skill | DEFER until agent APIs | MEDIUM |
| CMP-013 | multi-version matrix | controlled Python 3.11 | KEEP ours | HIGH |
| CMP-014 | static→browser→stealth | deterministic→OCR→future AI | KEEP ours | HIGH |

---

# 8. Agent debate decisions

## DEBATE KT-001 — Replace job queue
**Proposal:** priority scheduler + checkpoint system.  
**Support:** priorities, pause/resume, in-flight snapshots.  
**Opposition:** financial jobs require DB durability, tenant scope, idempotency and auditable outcomes.  
**Cost/Risk:** HIGH/HIGH.  
**Benefit:** LOW/MEDIUM.  
**Lead:** **REJECT.**

## DEBATE KT-002 — General fuzzy adaptive extraction
**Proposal:** choose similar fields after format drift.  
**Support:** resilient to changing supplier formats.  
**Opposition:** turns uncertainty into authoritative financial facts.  
**Alternatives:** deterministic update; advisory drift warning; human-confirmed AI suggestion.  
**Lead:** **ADAPT principle only.**

## DEBATE KT-003 — Honor downstream Retry-After
**Proposal:** failed handler may request a minimum next-attempt delay.  
**Reference evidence:** AutoThrottle parses numeric/date header; hint never speeds the crawler up.  
**Our evidence:** webhook response headers are discarded.  
**Cost/Risk/Benefit:** LOW / LOW / MEDIUM-HIGH.  
**Lead:** **ADAPT NOW.**

## DEBATE KT-004 — Concurrent worker fan-out
**Proposal:** run many jobs concurrently in one process.  
**Opposition:** existing worker lanes/horizontal processes already scale; measured DB contention warns against unmeasured concurrency.  
**Lead:** **REJECT FOR NOW.**

## DEBATE KT-005 — Global SessionManager
**Proposal:** one manager for every integration.  
**Opposition:** billing, extraction, storage, email and SSO have different domain contracts.  
**Lead:** **REJECT.**

## DEBATE KT-006 — Add Bandit because Scrapling uses it
**Support:** source-level security scan.  
**Opposition:** likely overlap/noise; InvoiceIQ already has strong targeted gates.  
**Lead:** **DEFER; measure Bandit vs Ruff-S signal first.**

## DEBATE KT-007 — AI engineering provenance
**Proposal:** formal policy.  
**Cost/Risk/Benefit:** LOW / LOW / HIGH.  
**Lead:** **ADOPT NOW.**

---

# 9. Prioritized backlog

| ID | Priority | Reference lesson | InvoiceIQ problem | Adaptation | Complexity | Risk | Status |
|---|---|---|---|---|---|---|---|
| KT-001 | P2 | Retry-After backpressure | webhook hint discarded | generic minimum retry hint | S | L | PATCH READY |
| KT-002 | P2 | AI contribution provenance | no formal dev-AI policy found | add policy | S | L | DOC READY |
| KT-003 | P2 | permanent learning | findings otherwise disappear | knowledge + pattern docs | S | L | DOC READY |
| KT-004 | P2 | response replay | AP parser changes need known-input replay | sanitized test corpus | M | L/M | PLANNED |
| KT-005 | P2 | adaptive memory | transport has drift baseline; AP apparently lacks one | design AP confirmed-output baseline | M/L | M | DESIGN DEFERRED |
| KT-006 | P3 | bounded fan-out | future async batch risk | invariant at first real site | S/site | L | DEFER |
| KT-007 | P3 | rich run stats | aggregate queue metrics can hide one kind | per-kind duration/attempt metrics | M | L | DEFER |
| KT-008 | P3 | CI fan-in | sequential lint diagnostics | aggregate independent checks | S/M | L | DEFER |
| KT-009 | P3 | source security scan | no dedicated source security linter | signal evaluation first | S | L | DEFER |
| KT-010 | P3 | agent skill | future agent APIs need rules | InvoiceIQ agent contract | M | M | DEFER |
| KT-011 | P4 | optional bundles | future OCR/AI package weight | worker-specific profiles only if measured | L | M | FUTURE |

No P0/P1 defect was discovered solely from the Scrapling comparison.

---

# 10. Lead Developer implementation orders

## TASK KT-001 — Receiver-directed webhook retry

**Objective:** respect a receiver's explicit recovery window while retaining InvoiceIQ's existing durable queue.

**Reference lesson:** Scrapling treats `Retry-After` as downstream backpressure and never lets it shorten local delay.

**Current implementation:** `_http_post()` returns status/text only; a non-2xx raises generic failure; jobs apply only local exponential backoff.

**Target:** numeric seconds and HTTP-date are parsed; invalid/non-finite values ignored; effective retry delay = `max(local_backoff, downstream_hint)`.

**Files:**
- `backend/app/services/jobs.py`
- `backend/app/services/webhooks.py`
- `backend/tests/test_jobs.py`
- `backend/tests/test_webhooks.py`

**Do not change:** DB schema, idempotency, max attempts/dead-letter semantics, webhook payload/signature, success handling, SSRF guard.

**Tests:**
1. 120s hint makes attempt-1 retry at 120s;
2. 5s hint does not shorten 30s local backoff;
3. numeric/date parsing;
4. invalid/negative/non-finite input;
5. 429 integration;
6. existing jobs/webhook tests;
7. Ruff/format/mypy/full CI.

**Rollback:** one four-file code/test commit; no migration.

---

## TASK KT-002 — AI engineering policy

Add `docs/AI-ENGINEERING-POLICY.md`.

Requirements:
- record material AI assistance/provenance;
- human/Lead Developer can explain the change;
- concrete evidence required;
- never weaken a gate to make output pass;
- no invented tax/legal/commercial policy;
- finance/tenant/authz/audit/concurrency invariants explicitly reviewed;
- production/customer data stays under ADR-0027;
- external code requires license/dependency/security review;
- generated baselines cannot self-approve.

---

## TASK KT-003 — Permanent knowledge base

Add:
- `docs/reference-repository-learnings.md`
- `docs/engineering-pattern-library.md`

Later repository analyses append observed/adopted/rejected patterns rather than overwrite them.

---

## TASK KT-004 — AP extraction replay corpus

Test-only design:
- sanitized original input;
- expected normalized output;
- provider/method;
- line/header field expectations;
- warnings/provenance classes.

Every fixture must pass existing PII quarantine.

---

## TASK KT-005 — AP extraction anti-drift design

Mirror the transport philosophy:
- baseline only after human-confirmed interpretation;
- re-extraction is advisory;
- money compared as Decimal;
- identity compared exactly after approved normalization;
- line-set drift always visible;
- human audited rebaseline;
- never automatically mutate a booked/confirmed invoice.

Do not implement until its data grain and retention policy are reviewed.

---

# 11. Status

## Reference analysis
Status: **COMPLETE for this transfer cycle**  
Relevant architecture understanding: **~95%**

Studied:
README, pyproject, package tree, fetchers, engines, Request, Scheduler, checkpoints, AutoThrottle, CrawlerEngine, SessionManager, adaptive parser/storage, response cache, stats, scheduler/throttle tests, CI, pre-commit, Docker/release, AI policy, agent skill and license.

Patterns: **18**  
High-impact debates: **7**  
Explicit anti-copy rejections: **10**

## Our-system improvement
Approved now: **KT-001, KT-002, KT-003**  
Next designs: **KT-004, KT-005**

Repository implementation status: **BLOCKED by connected GitHub App write permission (branch creation returned 403).**

Commit-ready artifacts are prepared separately.

---

# 12. Final mission answer

After studying Scrapling, the changes that make InvoiceIQ measurably better **without unnecessary complexity** are:

1. **Honor downstream `Retry-After` through the existing durable queue.**
   - No new infrastructure/schema.
   - Integration retries become friendlier and more correct.

2. **Formalize AI engineering provenance/evidence.**
   - Development speed no longer weakens accountability.

3. **Retain cross-repository learning in a permanent pattern library.**
   - Future agents do not repeatedly rediscover or re-propose rejected architecture.

4. **Next: build a sanitized AP extraction replay corpus.**
   - Parser changes become reproducible offline against known shapes.

5. **Next: design AP drift detection using the transport module's existing safe advisory/rebaseline model.**
   - Parser drift becomes visible without fuzzy automatic financial mutation.

The most important lesson is not a Scrapling class. It is disciplined recoverability and escalation: preserve work identity, make expensive behavior explicit, react to downstream pressure, replay external inputs during development, and make adaptation visible rather than magical. InvoiceIQ already has the stronger financial-integrity substrate; these adaptations add useful behavior without replacing it.


---

# Repository 2 — paperless-ngx/paperless-ngx

**Repository:** paperless-ngx/paperless-ngx  
**URL:** https://github.com/paperless-ngx/paperless-ngx  
**Reference commit:** `154a1932d84a26cc741d1dee353096515590a0b3` (`dev`)  
**Analysis date:** 2026-09-06  
**Our system/base:** `kristapsgoncoronoks-ship-it/Bid_it` at `d30f90b69566b4d9055e597bd824932e25e4daf6`

## Repository purpose

Paperless-ngx is a document-management system that turns incoming scans/files/mail into a searchable digital archive. Its main engineering strengths are not financial accounting or multi-tenant SaaS isolation. They are:

- document consumption/orchestration;
- parser/OCR lifecycle;
- progress/task visibility;
- document versions/trash;
- workflow-driven metadata;
- full-text search;
- outbound network hardening;
- operational packaging and CI discipline.

## Architecture map

```text
Angular SPA
   |
   +--> versioned Django REST API
   +--> authenticated status WebSocket
              |
              v
       Django/DRF monolith
              |
     +--------+---------+--------------------+
     |                  |                    |
 documents          paperless_mail      auth/config/admin
     |
     +--> document models / metadata / versions / trash
     +--> workflow triggers + actions
     +--> parser registry
     +--> search abstraction
     |
     v
 Celery asynchronous tasks
     |
     +--> consume_file
     |      |
     |      +--> preflight
     |      +--> ASN/barcode/collation
     |      +--> workflow metadata override
     |      +--> consumer
     |
     +--> indexing/reindexing
     +--> classifier training
     +--> mail polling
     +--> trash/scheduled workflows
     |
     v
 external/local processing
 OCRmyPDF/Tesseract/Tika/Gotenberg/remote OCR/AI
     |
     v
 originals + archive rendition + thumbnail + DB + Tantivy index
```

Redis is used for Celery and Channels. Tantivy is a separate full-text index. The backend remains a modular monolith rather than a microservice system.

## Major runtime flow

```text
file / mail / API upload
        |
        v
Celery consume_file
        |
        v
temporary working directory
        |
        v
ordered consume plugins
        |
        +--> preflight: exists / duplicate / directories
        +--> barcode/collation/ASN if applicable
        +--> consumption workflow metadata overrides
        |
        v
consumer
        |
        +--> copy source to scratch
        +--> libmagic MIME detection
        +--> parser registry chooses parser
        +--> parse/OCR
        +--> page count/date/content
        +--> generate thumbnail
        +--> optionally generate archive rendition
        +--> classifier suggestions
        |
        v
transactional persistence section
        |
        +--> document/version row
        +--> metadata/permissions/custom fields
        +--> final media placement
        +--> post-consume hooks
        |
        v
search indexing
        |
        v
progress/task result + UI
```

## Top engineering ideas

### PNGX-001 — Ordered consume plugin contract
**Category:** Architecture / Backend  
**Evidence:** `src/documents/tasks.py`, `src/documents/plugins/base.py`

Paperless gives consume steps a small lifecycle: `able_to_run`, `setup`, `run`, `cleanup`; cleanup is guaranteed and a plugin can intentionally halt the chain.

**Problem solved:** document ingestion accumulates optional preprocessing steps without turning one consumer function into an unmaintainable conditional tree.

**Advantages:** explicit lifecycle; isolated cleanup; metadata flows between steps; optional stages are composable.

**Weaknesses:** plugin frameworks add indirection; too many tiny plugins make execution hard to trace.

**Transferability:** MEDIUM.

**InvoiceIQ decision:** **DEFER.** We already have explicit upload guards, `ExtractionProvider`, capture progress and durable jobs. Introduce a capture-step abstraction only when at least two additional independent pre/post extraction steps otherwise duplicate orchestration.

---

### PNGX-002 — Cheap deterministic preflight before expensive OCR
**Category:** Reliability / Performance  
**Evidence:** `src/documents/consumer.py` (`ConsumerPreflightPlugin`)

Checks file existence, checksum duplication and required directories before parser/OCR work.

**Transferability:** HIGH, but **already implemented in spirit**.

InvoiceIQ scans, hashes, deduplicates and enforces upload limits before the async extraction path. Keep this ordering invariant.

**Decision:** KEEP OUR APPROACH.

---

### PNGX-003 — Scratch-copy processing
**Category:** Reliability / File handling  
**Evidence:** `src/documents/consumer.py`

Paperless parses a working copy in a temporary directory and keeps the inbound source until successful persistence.

**Principle:** expensive/untrusted tooling should mutate disposable working material, never the source of truth.

**Transferability:** HIGH.

InvoiceIQ already stores immutable, content-addressed source bytes before asynchronous parse. This is stronger for a multi-tenant SaaS.

**Decision:** KEEP OUR APPROACH.

---

### PNGX-004 — Conditional expensive rendition generation
**Category:** Performance / Document UX  
**Evidence:** `should_produce_archive()` in `src/documents/consumer.py`

Paperless avoids archive/PDF conversion for born-digital PDFs when it adds no value, but creates a normalized rendition for scans/images when useful.

**Transferability:** MEDIUM.

**Potential InvoiceIQ use:** page thumbnails / normalized review renditions should be generated only for formats or review cases that need them.

**Decision:** ADAPT when thumbnail/rendition backlog is implemented; do not generate every derivative for every document.

---

### PNGX-005 — Parser capability registry + scoring
**Category:** Architecture  
**Evidence:** `src/paperless/parsers/registry.py`

Built-in and external parsers advertise supported MIME types and score suitability. Remote parsers can be excluded by policy.

**Advantages:** extensible, attributable, capability-aware.
**Disadvantages:** score conflicts can make parser selection less obvious; arbitrary third-party entrypoints execute code inside the server process.

**Transferability:** LOW/MEDIUM.

InvoiceIQ needs deterministic, auditable parser choice for financial documents.

**Decision:** **REJECT third-party entrypoint loading and score-based ambiguity.** Keep explicit provider registration/order. Consider capability metadata only if the provider set becomes large.

---

### PNGX-006 — Persisted + realtime task progress
**Category:** UX / Operations  
**Evidence:** `src/documents/plugins/helpers.py`, `src/paperless/consumers.py`

Paperless emits typed progress with task/document identity and permission metadata, then filters WebSocket updates by the authenticated viewer.

**Transferability:** MEDIUM.

InvoiceIQ already persists `stage`, `pages_done`, `pages_total` and exposes human wording such as “Recognising page N of M.”

**Decision:** KEEP persisted polling at current scale. WebSockets/Redis are not justified solely for animation.

---

### PNGX-007 — Background task correlation in every log
**Category:** Observability  
**Evidence:** `src/paperless/logging.py`

A ContextVar carries the consume task ID so internal logs automatically correlate to one background execution.

**Problem in InvoiceIQ:** HTTP logs carry `request_id`, but worker handler logs do not automatically inherit `job_id`/`job_kind`.

**Transferability:** HIGH.

**Decision:** **ADAPT NOW — P2.** Add job context fields to production structured logs without changing job semantics.

---

### PNGX-008 — Operational task inbox
**Category:** UX / Operations  
**Evidence:** `src-ui/src/app/components/admin/tasks/tasks.component.ts`

Tasks are grouped into Needs attention / In progress / Completed, filterable by type and source, with visible failure/result detail.

**Transferability:** MEDIUM/HIGH.

InvoiceIQ has a tenant-scoped `/jobs` API and strong queue health, but no `Jobs` page in the current frontend inventory.

**Decision:** P2/P3 product decision. Build an operations screen when customers/admins need self-service failure recovery; do not expose raw internal payloads.

---

### PNGX-009 — Pinned-IP outbound HTTP transport
**Category:** Security  
**Evidence:** `src/paperless/network.py`, `src/documents/workflows/webhooks.py`

Paperless resolves a hostname, verifies all answers are public, then rewrites the actual HTTP connection to a vetted IP while preserving the original Host header and TLS SNI. Redirects are disabled.

**Problem in InvoiceIQ:** current webhook validation resolves and checks the hostname, then ordinary `httpx` resolves again when connecting. The repository's own audit already records this as `SEC-009`: a narrow DNS-rebinding/TOCTOU window.

**Transferability:** HIGH.

**Decision:** **ADAPT NOW — P3 security hardening** (kept at the repo's existing severity; do not inflate it). Prepared patch closes the connect-time race without Redis/new services.

---

### PNGX-010 — Explicit special-network blocking
**Category:** Security  
**Evidence:** `src/paperless/network.py`

Paperless explicitly blocks CGNAT `100.64.0.0/10` and the NAT64 well-known prefix `64:ff9b::/96`, not just ordinary private/loopback ranges.

**Transferability:** HIGH, low complexity.

**Decision:** include in PNGX-009.

---

### PNGX-011 — Permission filtering inside the search engine
**Category:** Security / Search  
**Evidence:** `src/documents/search/_backend.py`

Permission constraints are part of the Tantivy query, not applied after search results are returned.

**Principle:** a secondary index is another data boundary and must enforce the same visibility model.

**Transferability:** HIGH principle / LOW current implementation need.

**Decision:** store as future rule. If InvoiceIQ adds external full-text/vector search, tenant and capability filtering must happen in the search query itself, with DB/RLS re-check before consequential access.

---

### PNGX-012 — Self-healing secondary index
**Category:** Reliability  
**Evidence:** `src/documents/tasks.py`, `src/documents/search/_backend.py`

Search lock contention/failure is retried separately and can be repaired without failing the authoritative document record.

**Principle:** a rebuildable projection should not become the source of truth.

**Transferability:** HIGH principle.

**Decision:** future external search/analytics projections must be reconstructible from PostgreSQL/object storage and failures must not corrupt financial state.

---

### PNGX-013 — Document version chain
**Category:** Data model  
**Evidence:** `Document.root_document`, `version_index`, `version_label`; `select_for_update` during version creation.

**Advantages:** original history retained, latest effective content available, concurrent version index allocation protected.

**Transferability:** LOW for accounting documents, MEDIUM for ancillary evidence.

**Decision:** **REJECT for booked invoice semantics.** Revised supplier invoices, credit notes and accounting corrections are business events, not file replacement. For contracts/evidence, immutable linked attachments may be simpler than a general version graph.

---

### PNGX-014 — Soft delete / trash
**Category:** UX / Data lifecycle  
**Evidence:** `Document(SoftDeleteModel)` and trash API.

**Transferability:** LOW/MEDIUM.

Financial retention/deletion is policy-driven and InvoiceIQ already has archive/retention logic.

**Decision:** do not copy a generic trash model into statutory financial records. Use domain-specific archive/retention rules.

---

### PNGX-015 — Pre-save workflow metadata overrides
**Category:** Workflow / UX  
**Evidence:** `WorkflowTriggerPlugin` before final consumer.

**Principle:** automation can supply metadata to the canonical ingest path instead of creating a parallel ingest implementation.

**Transferability:** MEDIUM/HIGH.

InvoiceIQ's automation engine is intentionally bounded and versioned. If capture-routing automation is added, make it call the same canonical capture service with explicit overrides; do not fork extraction.

**Decision:** ADAPT PRINCIPLE, DEFER CODE.

---

### PNGX-016 — Bounded vs open workflow engine comparison
**Category:** Architecture  
**Evidence:** Paperless workflow trigger/action models versus InvoiceIQ `AutomationRule`.

Paperless supports consumption/document-added/document-updated/scheduled triggers and a broader set of actions. InvoiceIQ uses a closed trigger catalog, JSON-Logic subset, immutable published versions and a fixed action catalog with no scripting/loops/branching.

**Decision:** **KEEP INVOICEIQ.** The finance/SaaS threat model benefits from the narrower execution model and durable version history.

---

### PNGX-017 — Tesseract/OpenMP oversubscription control
**Category:** Performance  
**Evidence:** Paperless settings force `OMP_THREAD_LIMIT=1` because multiple worker/process concurrency is already managed outside Tesseract.

InvoiceIQ OCR is page-sequential but multiple workers may run simultaneously.

**Transferability:** MEDIUM.

**Decision:** **DEFER until benchmarked.** Measure 1/2/4 OCR jobs with and without `OMP_THREAD_LIMIT=1`; adopt only if throughput/latency improves.

---

### PNGX-018 — Immutable GitHub Action references
**Category:** Supply chain / DevOps  
**Evidence:** Paperless workflows use action commit SHAs with version comments.

InvoiceIQ workflows currently use mutable major tags (`actions/checkout@v7`, `docker/*@v4/v6/v7`, etc.).

**Transferability:** VERY HIGH.

**Decision:** **ADOPT NOW — P2.** A deterministic application script is included with SHA values resolved from the exact major tags on 2026-09-06.

---

### PNGX-019 — Protected development branch with required checks
**Category:** DevOps / Governance  
**Evidence:** Paperless `dev` branch protection; InvoiceIQ `main` currently reports `protected:false`.

InvoiceIQ already lists “Branch protection on main” in `docs/DECISIONS-NEEDED.md`.

**Transferability:** VERY HIGH.

**Decision:** **P1 production-readiness owner action.** Require PR + existing CI checks; block force push/delete. This is repository configuration, not application code.

---

### PNGX-020 — Static analysis of code AND CI workflows
**Category:** Security / CI  
**Evidence:** `ci-static-analysis.yml` uses Semgrep CE and zizmor.

**Transferability:** MEDIUM.

InvoiceIQ already has PII scanning, Ruff, mypy, pip-audit, Postgres/RLS/concurrency/performance gates.

**Decision:** **DEFER mandatory adoption.** Run Semgrep + zizmor non-blocking first; keep only rules with demonstrated signal.

---

### PNGX-021 — Path-aware CI
**Category:** CI performance  
**Evidence:** Paperless backend CI detects relevant file changes and can skip expensive backend matrices.

**Transferability:** MEDIUM.

InvoiceIQ's CI is intentionally broad and its cross-layer structural gates are valuable.

**Decision:** DEFER. Optimize CI only after measuring runner cost/feedback latency; do not accidentally skip cross-layer invariants.

---

### PNGX-022 — Multi-runtime test matrix
**Category:** Compatibility  
**Evidence:** Python 3.11–3.14 matrix.

**Transferability:** LOW.

Paperless is distributed/self-hosted across environments; InvoiceIQ controls its production runtime.

**Decision:** REJECT until runtime portability is a product requirement.

---

### PNGX-023 — Custom fields on documents
**Category:** Product / Data model  
**Evidence:** Paperless `CustomField`/`CustomFieldInstance`.

**Transferability:** LOW for core finance.

**Decision:** REJECT generic field creep for invoices/payments. Add typed domain fields or bounded custom metadata only where a real customer requirement exists.

---

### PNGX-024 — Full-text Tantivy search
**Category:** Search / Performance  
**Evidence:** `src/documents/search/_backend.py`.

**Transferability:** LOW today, potentially HIGH later for OCR archive search.

**Decision:** DEFER. PostgreSQL/domain filtering remains simpler at current product scope. If OCR corpus search becomes a key user journey, benchmark PostgreSQL FTS first before adding another stateful index.

---

## Patterns we should NOT adopt from Paperless

1. **Celery + Redis as queue replacement** — solves distributed worker orchestration we already solve with a durable PostgreSQL queue and lanes.
2. **Django/Angular migration** — technology replacement with no product benefit.
3. **Third-party Python parser entrypoints** — arbitrary in-process code is an unacceptable SaaS extension model.
4. **Score-based parser ambiguity for financial capture** — deterministic provenance is preferable.
5. **Generic document trash for statutory accounting state** — retention must remain domain-specific.
6. **Generic document versioning for invoices** — accounting corrections are business events, not file revisions.
7. **Tantivy now** — extra consistency/index security burden without evidence current search is insufficient.
8. **Redis/WebSockets solely for progress animation** — persisted capture progress already solves the actual user problem.
9. **Paperless's single-host sensitive-document security posture** — InvoiceIQ must retain tenant isolation and SaaS controls.
10. **Open-ended workflow power** — retain the bounded action/trigger catalog and no scripting.

## Cross-repository learning: Scrapling + Paperless + InvoiceIQ

### Work orchestration
- Scrapling: process-local priority scheduler/checkpoints.
- Paperless: Celery/Redis distributed tasks.
- InvoiceIQ: PostgreSQL durable queue with tenant scope, leases, idempotency, dead letters and lanes.

**Decision:** InvoiceIQ's queue is the best fit. Borrow backpressure/replay/progress/correlation principles, not queue technology.

### Parser extensibility
- Scrapling: multiple fetch/parse execution strategies.
- Paperless: MIME/capability scoring + third-party entrypoints.
- InvoiceIQ: deterministic provider registry with per-field provenance.

**Decision:** keep deterministic provider selection. Add capability metadata only when needed; no arbitrary plugin code.

### Adaptation after input drift
- Scrapling: fuzzy relocation.
- Paperless: classifier/workflow suggestions and reprocessing.
- InvoiceIQ: advisory transport extraction baseline + explicit audited rebaseline.

**Decision:** InvoiceIQ's advisory/human-confirmed approach remains safest for financial facts.

### Background observability
- Scrapling: CrawlStats.
- Paperless: task ID correlation + task inbox + realtime progress.
- InvoiceIQ: request IDs, queue SLOs, persisted capture progress.

**Decision:** add `job_id`/`job_kind` log correlation; defer WebSockets and richer task UI until operational demand.

### Outbound integration safety
- Scrapling lesson: honor `Retry-After`.
- Paperless lesson: pin validated DNS into the actual connection.
- InvoiceIQ current: HMAC + SSRF precheck + durable retry + idempotency.

**Decision:** combine the principles inside InvoiceIQ's existing webhook subsystem: receiver-directed backoff (Scrapling patch) + pinned connect-time DNS (Paperless patch).

## Engineering debates

### DEBATE PNGX-D1 — Replace DB queue with Celery/Redis
**Support:** mature distributed ecosystem, chords, routing, scheduling.
**Opposition:** another stateful dependency; duplicates durability/leases/retry already built; tenant context and exact semantics already tested.
**Cost:** HIGH  
**Risk:** HIGH  
**Expected benefit:** LOW/MEDIUM at current scale  
**Lead decision:** **REJECT.**

### DEBATE PNGX-D2 — General consume plugin framework
**Support:** clean optional stages and cleanup.
**Opposition:** current capture pipeline is still understandable; abstraction before repeated pain is cargo culting.
**Cost:** MEDIUM  
**Risk:** MEDIUM  
**Benefit:** MEDIUM only if more stages appear  
**Lead:** **DEFER.**

### DEBATE PNGX-D3 — Pinned outbound transport
**Support:** closes an already-documented DNS validation/connect race; isolated and testable.
**Opposition:** admin-controlled webhook + small timing window; repository previously rated it LOW/P3.
**Cost:** LOW/MEDIUM  
**Risk:** LOW  
**Benefit:** MEDIUM security  
**Lead:** **ADAPT, preserve P3 severity, implement because scope is small and native.**

### DEBATE PNGX-D4 — WebSockets/Redis for capture progress
**Support:** realtime UX.
**Opposition:** InvoiceIQ already persists truthful stages/page counts; Redis creates an operational dependency for cosmetic latency.
**Lead:** **REJECT FOR NOW.**

### DEBATE PNGX-D5 — Branch protection + immutable Actions
**Support:** prevents direct untested production changes and reduces action-tag supply-chain mutability.
**Opposition:** can slow a solo maintainer if approval settings are too strict.
**Alternative:** require PR + checks, but do not require another human approval until team size supports it.
**Cost:** LOW  
**Risk:** LOW  
**Benefit:** HIGH  
**Lead:** **ADOPT.**

### DEBATE PNGX-D6 — Job correlation context
**Support:** one job can produce logs across many services; current `request_id` is `-` in workers.
**Opposition:** small extra fields in production JSON.
**Cost:** LOW  
**Risk:** LOW  
**Benefit:** MEDIUM/HIGH operationally  
**Lead:** **ADAPT NOW.**

### DEBATE PNGX-D7 — Tantivy
**Support:** excellent OCR full-text search.
**Opposition:** second stateful consistency/security plane; current product needs structured finance search more than arbitrary archive search.
**Lead:** **DEFER.**

## Prioritized improvements

| ID | Priority | Improvement | Complexity | Risk | Status |
|---|---|---|---|---|---|
| PNGX-P1-01 | P1 | Protect `main`: PR + existing required CI checks; block force push/delete | S | L | OWNER/REPO SETTING |
| PNGX-P2-01 | P2 | Pin all GitHub Actions to immutable SHAs | S | L | APPLY SCRIPT READY |
| PNGX-P2-02 | P2 | Add `job_id` + `job_kind` structured log correlation | S | L | PATCH READY |
| PNGX-P3-01 | P3 | Pin webhook connection to vetted DNS IP | S/M | L | PATCH READY |
| PNGX-P2-03 | P2 | Evaluate customer/admin job “Needs attention” UI | M | L | DESIGN |
| PNGX-P3-02 | P3 | Conditional thumbnails/renditions | M | L/M | DEFER TO DOCUMENT UX |
| PNGX-P3-03 | P3 | Semgrep + zizmor non-blocking trial | S | L | EVALUATE |
| PNGX-P3-04 | P3 | OCR `OMP_THREAD_LIMIT=1` benchmark | S | L | BENCHMARK |
| PNGX-P4-01 | P4 | Full-text OCR archive search | L | M | FUTURE |
| PNGX-P4-02 | P4 | Capture-step/plugin abstraction | M/L | M | FUTURE IF NEEDED |

No new P0 defect was discovered. Paperless provided direct evidence for two already-known InvoiceIQ owner/security gaps: branch protection and DNS pinning.

## Lead Developer implementation orders

### TASK PNGX-P2-01 — Immutable Actions

**Objective:** replace mutable action tags in the three InvoiceIQ workflows with exact commit SHAs corresponding to the currently selected majors.

**Files:** `.github/workflows/ci.yml`, `.github/workflows/pii-history.yml`, `.github/workflows/release.yml`.

**Do not change:** action major versions, job graph, deploy behavior, VR baseline push behavior.

**Acceptance:**
- no known `actions/*@vN` or `docker/*@vN` reference remains for the covered actions;
- all workflow YAML parses;
- CI still reaches the same jobs;
- VR baseline job retains credentials because it intentionally commits to a working branch.

### TASK PNGX-P2-02 — Job log correlation

**Objective:** every log emitted while a durable job handler runs can be tied to its job ID and kind without every service passing them manually.

**Files:** `backend/app/core/observability.py`, `backend/app/services/jobs.py`, tests.

**Target:** ContextVars `job_id_ctx`, `job_kind_ctx`; production JSON includes them only during a job; set/reset around handler execution.

**Do not change:** request IDs, handler signature, job schema, tenant scope, retry behavior.

**Acceptance:** handler sees correct context; context is cleared after execution/failure; formatted JSON contains the fields during a job.

### TASK PNGX-P3-01 — Connect-time DNS pinning

**Objective:** close the validation-to-connect DNS rebinding window for outbound webhooks.

**Files:** new `backend/app/core/outbound_http.py`, `backend/app/services/webhooks.py`, tests.

**Target:** resolve at transport send time, reject any non-public answer, connect to the vetted IP, preserve original Host/TLS SNI, disable redirects explicitly.

**Do not change:** webhook HMAC, idempotency, delivery payload, queue, endpoint model, current registration-time failure semantics.

**Acceptance:**
- public hostname is rewritten to public IP for connection;
- original Host/SNI remain hostname;
- private, loopback, CGNAT and NAT64 answers are rejected;
- a public precheck followed by private connect-time DNS is blocked;
- DNS failure is a network failure/retry, not an internal connection;
- successful webhook behavior unchanged.

### TASK PNGX-P1-01 — Branch protection

Repository configuration:
- require pull request before merge;
- require current CI checks (`pii-scan`, `lint`, `backend`, `postgres`, `frontend`, `frontend-e2e`, `docker-build`);
- require branch up-to-date before merge;
- block force pushes;
- block deletion;
- keep emergency bypass limited to repository admin;
- if a single maintainer, do not require a second human approval yet; add approval requirement when team size supports it.

## Status

### Reference repository analysis
Architecture understanding for transferable areas: **~94%**  
Key files/modules inspected: README, pyproject, frontend package, Django settings/URLs, consume task/consumer/plugin contract, progress/WebSocket, parser registry, document/version model, workflows, outbound network transport, search backend, mail ingestion, CI/backend/static-analysis/docker workflows.

Patterns identified: **24**  
High-transfer principles: **13**  
Direct adoptions approved: **4**  
Explicit rejections/deferments: **10+**

### Our-system improvement
P0: 0  
P1: 1 repo-governance action  
P2: 3 strong changes/designs  
P3: 4 targeted improvements/evaluations  
P4: 2 future ideas

Repository branch/commit status: **BLOCKED — GitHub integration returned 403 on branch creation.**


---

# Repository 3 — twentyhq/twenty

**Repository:** `twentyhq/twenty`  
**URL:** https://github.com/twentyhq/twenty  
**Reference commit:** `59a777ea6774d3377753b982d09b6605dca50afe` (`main`)  
**Analysis date:** 2026-09-06  
**Our system/base:** `kristapsgoncoronoks-ship-it/Bid_it` at `d30f90b69566b4d9055e597bd824932e25e4daf6`

## Repository purpose

Twenty is an open-source CRM/application platform designed to be deeply extensible rather than a fixed CRM schema. It allows applications to define objects, fields, views, workflows, command/navigation items, logic functions, roles and AI agents as metadata/code.

The engineering reason for the architecture is important: Twenty is solving **per-workspace product extensibility**. That requirement explains its metadata engine, workspace-specific PostgreSQL schemas, generated GraphQL API, app sync/versioning, Redis/BullMQ queueing and sandboxed/custom application surfaces. InvoiceIQ does not share that requirement in its financial core, so those mechanisms are evidence to learn from rather than defaults to copy.

## Architecture map

```text
React frontend / Twenty UI
        |
        +--> GraphQL / REST / metadata APIs
        |
        v
NestJS server
        |
        +--> Core modules
        |      auth / sessions / metrics / files / queue / cache / secure HTTP
        |
        +--> Metadata engine
        |      objects / fields / views / roles / permissions
        |      workflows / command menu / navigation / apps
        |      logic functions / skills / AI agents
        |
        +--> Dynamic workspace GraphQL schema builder
        |
        +--> Workspace ORM + per-workspace PostgreSQL schema
        |
        +--> BullMQ / Redis workers
        |
        +--> Custom logic-function execution
        |
        v
PostgreSQL + Redis + external services
```

The frontend and backend are in an Nx monorepo with shared TypeScript packages and a reusable design system.

## Major execution paths

### Normal workspace record flow

```text
USER
  ↓
React UI
  ↓
GraphQL / REST operation
  ↓
auth + workspace context + permissions
  ↓
workspace-generated GraphQL schema / resolver
  ↓
metadata-driven object/field model
  ↓
workspace ORM
  ↓
workspace PostgreSQL schema
  ↓
response + UI state
```

### Application metadata flow

```text
APP SOURCE
  ↓
twenty plan / dev / apply
  ↓
metadata diff
  ↓
create / update / destroy plan
  ↓
validation + destructive-change visibility
  ↓
serialized workspace sync
  ↓
metadata + physical schema changes
```

### AI agent flow

```text
agent invocation
  ↓
workspace / actor / user context
  ↓
agent role + optional run-as role
  ↓
tool catalog filtered by permissions
  ↓
model execution
  ↓
max-step + billing/credit guard
  ↓
tool execution with metrics / actor attribution
  ↓
result
```

## Top engineering ideas

### TWENTY-001 — Metadata as a first-class application layer
**Category:** Architecture  
**What:** objects, fields, views, workflows, roles, commands, agents and apps are represented through a metadata engine rather than hard-coded only in product modules.  
**Problem solved:** a CRM platform must let different workspaces behave like different applications.  
**Advantages:** extreme extensibility, deployable apps, configurable UX.  
**Disadvantages:** much larger consistency, migration, cache and permission surface.  
**Transferability:** LOW to InvoiceIQ financial core; MEDIUM for future add-on modules.  
**Decision:** REJECT as core architecture. Preserve typed financial domain models.

### TWENTY-002 — Stable universal identifiers for evolvable metadata
**Category:** Architecture / Migration  
**What:** application metadata is addressed by stable universal identifiers across syncs/environments.  
**Problem solved:** names and database IDs change; migrations and references need a durable identity.  
**Transferability:** MEDIUM.  
**InvoiceIQ:** use stable IDs for any future configurable object/action/plugin definitions, not for ordinary typed domain rows.

### TWENTY-003 — Plan before apply
**Category:** DevOps / Data integrity  
**What:** metadata sync has a read-only plan that computes exact create/update/destroy impact before writing. Destructive operations are explicit.  
**Problem solved:** AI/manual configuration changes can otherwise destroy schema/data before an operator understands the diff.  
**Transferability:** HIGH principle.  
**InvoiceIQ evidence:** current Alembic migrations already use fail-closed data preflights, including DB-011/017, but there is no general Terraform-style preview layer.  
**Decision:** ADAPT AS MIGRATION POLICY, not a runtime metadata engine.

### TWENTY-004 — Serialize schema/config mutations
**Category:** Reliability  
**What:** application metadata sync is serialized per workspace rather than allowing multiple agents/processes to mutate the same metadata concurrently.  
**Problem solved:** racing schema/config writers.  
**Transferability:** HIGH principle.  
**InvoiceIQ:** apply to AI/developer deployment orchestration: one migration/deploy writer at a time. Current production deploy concurrency already moves in this direction.  
**Decision:** KEEP AS ENGINEERING RULE.

### TWENTY-005 — Context-aware command menu
**Category:** UX / Frontend  
**What:** Cmd/Ctrl+K provides centralized action discovery with context/permission-aware availability.  
**Problem solved:** users cannot discover actions/navigation in a large application solely through hierarchical menus.  
**Transferability:** VERY HIGH.  
**InvoiceIQ evidence:** one permission/module-filtered `navGroups` structure already exists in `Layout.tsx`; no backend global-search endpoint exists.  
**Decision:** ADAPT NOW as a navigation-only quick command palette over already-filtered destinations. No metadata DSL and no fake search backend.

### TWENTY-006 — Saved views separate data from presentation preferences
**Category:** UX / Product  
**What:** a view stores layout, fields, filters, sorting and grouping separately from the underlying records.  
**Problem solved:** operators repeatedly rebuild the same worklist.  
**Transferability:** MEDIUM/HIGH for invoice/claim/transport worklists.  
**Decision:** P3 product enhancement after measuring repeated filter use. Start with saved filters/columns, not generic Kanban/calendar infrastructure.

### TWENTY-007 — Workspace-specific PostgreSQL schemas
**Category:** Database  
**What:** each workspace has its own PostgreSQL schema because each can define different physical objects/fields.  
**Transferability:** LOW.  
**Decision:** REJECT. InvoiceIQ's shared schema + explicit `org_id` + ORM tenant guard + PostgreSQL FORCE RLS is better for a typed financial SaaS and avoids per-tenant DDL.

### TWENTY-008 — Dynamic per-workspace GraphQL schema
**Category:** API  
**What:** GraphQL types/resolvers are generated from workspace metadata.  
**Transferability:** LOW.  
**Decision:** REJECT. InvoiceIQ should keep typed REST/Pydantic contracts for money, tax, payment and approval invariants.

### TWENTY-009 — Workflow versions with a database-level active-version invariant
**Category:** Workflow / Data integrity  
**What:** workflow versions are immutable-ish lifecycle records and the DB ensures only one ACTIVE version per workflow/workspace.  
**InvoiceIQ evidence:** automation already snapshots each publish, versions executions and has one live rule row carrying `published_version`; it does not maintain multiple simultaneously-active version rows.  
**Transferability:** LOW as a new feature.  
**Decision:** KEEP OUR MODEL; no duplicate “active version” constraint is required because active state lives on `AutomationRule`, not version rows.

### TWENTY-010 — Bounded agent authorization context
**Category:** AI / Security  
**What:** AI execution resolves an attributable actor, an agent role and optional run-as role. No role means no registry tools; tools are filtered by permission.  
**Problem solved:** “AI has access to everything” is not an acceptable authorization model.  
**Transferability:** VERY HIGH for any future write-capable InvoiceIQ AI/MCP surface.  
**Decision:** ADOPT AS ARCHITECTURE LAW; no write-capable agent until this model exists.

### TWENTY-011 — Tool catalog + lazy tool loading
**Category:** AI / Performance  
**What:** open-ended agents can see a compact catalog and use constrained meta-tools to learn/execute only allowed tools rather than loading every schema into every prompt.  
**Problem solved:** prompt bloat and accidental broad capability exposure.  
**Transferability:** MEDIUM future.  
**Decision:** DEFER until InvoiceIQ has enough agent tools for tool-selection cost to be measured.

### TWENTY-012 — Explicit recursion guard for agent/workflow tools
**Category:** AI / Reliability  
**What:** agent tool sets intentionally exclude workflow-registry/navigation tool classes that could create circular/recursive execution.  
**Transferability:** HIGH future.  
**Decision:** any InvoiceIQ agent executor must maintain an explicit forbidden/re-entrant tool set and max-depth/step guard.

### TWENTY-013 — AI spend and step budgets are runtime controls
**Category:** AI / Commercial / Reliability  
**What:** agent execution checks billing/credit eligibility, decrements usage and stops on max steps or no credits.  
**Transferability:** HIGH when generative actions become paid runtime features.  
**Decision:** future AI cannot ship without per-tenant/user usage accounting and execution ceilings.

### TWENTY-014 — Tool-level telemetry
**Category:** Observability / AI  
**What:** tool execution duration and output-token characteristics are metrics, not only generic HTTP timings.  
**Transferability:** HIGH future.  
**Decision:** future agent/tool runtime must emit per-tool latency, error and cost metrics; generic request metrics are insufficient for agent debugging.

### TWENTY-015 — Custom UI is isolated from privileged secrets
**Category:** Security / Extensibility  
**What:** custom front components run in isolated browser contexts; secret variables remain server-side and privileged logic is called through server functions.  
**Transferability:** HIGH principle / LOW current need.  
**Decision:** if InvoiceIQ ever supports tenant-installed UI/apps, browser extensions must receive user-scoped capability only; secrets and application credentials stay server-side.

### TWENTY-016 — Executable tenant logic is a separate runtime concern
**Category:** Security / Architecture  
**What:** logic functions are transpiled/bundled and run through a dedicated driver/child-process path with temporary execution material, logs and timeouts.  
**Transferability:** LOW now.  
**Decision:** REJECT tenant-authored executable code in the current product. A financial SaaS should prefer declarative bounded automation. Revisit only with a product requirement and a hardened isolation model.

### TWENTY-017 — Connection-level SSRF enforcement
**Category:** Security  
**What:** outbound clients validate resolved addresses at the actual connection boundary, including redirect-created connections.  
**Transferability:** VERY HIGH.  
**Decision:** reinforces Paperless `PAT-022` and the prepared InvoiceIQ DNS-pinning patch. This is now a cross-repository consensus pattern.

### TWENTY-018 — Queue driver abstraction
**Category:** Backend / Scalability  
**What:** asynchronous use cases sit behind a message-queue abstraction with BullMQ and synchronous implementations.  
**Transferability:** LOW/MEDIUM.  
**Decision:** do not replace the InvoiceIQ PostgreSQL durable queue. The useful principle—domain code should not depend on broker internals—is already largely achieved by `jobs.enqueue` + handler registration.

### TWENTY-019 — Per-domain/job metrics
**Category:** Observability  
**What:** metrics have explicit keys for sync jobs, workflow/tool execution, failures and business infrastructure.  
**Transferability:** HIGH.  
**Decision:** extend InvoiceIQ metrics only around measured operational pain. Current first target remains job correlation/queue health already identified through Paperless.

### TWENTY-020 — Semantic API-breaking-change detection
**Category:** CI / API  
**What:** CI boots the PR and main versions independently, extracts live GraphQL/OpenAPI contracts and evaluates breaking changes.  
**Transferability:** MEDIUM/HIGH.  
**InvoiceIQ evidence:** a checked-in OpenAPI snapshot already fails on drift; therefore unintended drift is already caught.  
**Decision:** P3 enhancement: semantic “breaking vs additive” classification if/when external API compatibility becomes a contractual product promise.

### TWENTY-021 — Production-parity E2E can be expensive and selective
**Category:** Testing / CI  
**What:** heavy production-parity application E2E is dispatched on main/manual and selectively on labeled PRs.  
**Transferability:** MEDIUM.  
**Decision:** use only if InvoiceIQ acquires expensive external-provider E2E environments. Current deterministic mocked Playwright + Postgres CI is more cost-effective.

### TWENTY-022 — Path-aware/affected CI
**Category:** CI performance  
**What:** monorepo checks run only for affected areas.  
**Transferability:** MEDIUM.  
**Decision:** DEFER until CI duration/cost is a demonstrated developer bottleneck. Cross-layer gates must never be accidentally skipped.

### TWENTY-023 — Dependency overrides require explanation
**Category:** Supply chain  
**What:** dependency pin/resolution work is documented with the vulnerability/compatibility reason rather than being an unexplained override.  
**Transferability:** HIGH principle.  
**InvoiceIQ:** pip-audit already documents its one ignored advisory and reason.  
**Decision:** KEEP OUR PRACTICE; apply the same rule to future npm overrides.

### TWENTY-024 — Explicit loading/error/saving states for extension UI
**Category:** UX / Code quality  
**What:** app front-component guidance requires loading, empty, error, disabled and saving states to remain visible/recoverable.  
**Transferability:** HIGH.  
**InvoiceIQ:** CI already has a ratchet that prevents new `useQuery` pages without error states.  
**Decision:** KEEP OUR STRONGER ENFORCED VERSION.

### TWENTY-025 — Design-system and shared package boundaries
**Category:** Frontend / Code quality  
**What:** shared UI/types live outside feature code in dedicated monorepo packages.  
**Transferability:** LOW/MEDIUM.  
**InvoiceIQ:** already has a shared frontend design system; splitting it into a package would add build complexity without another consumer.  
**Decision:** KEEP CURRENT STRUCTURE.

## Patterns we should not adopt

1. **Schema-per-workspace** — required by Twenty's arbitrary custom objects, unnecessary for InvoiceIQ.
2. **Dynamic GraphQL for the financial core** — weakens explicit API/business contracts.
3. **Redis/BullMQ replacement** — no measured queue problem justifies a second stateful service.
4. **Nx/TypeScript backend migration** — technology churn, not an engineering improvement.
5. **Generic object/field metadata engine** — risks turning accounting invariants into configurable metadata.
6. **Tenant-authored JavaScript logic now** — major security/isolation surface.
7. **App marketplace/plugin runtime now** — no validated commercial requirement.
8. **Generic workflow graph** — InvoiceIQ's bounded automation is intentionally safer.
9. **Field/row permission DSL replacing typed capabilities/RLS** — current model is clearer for finance.
10. **AI agents with broad default tools** — future agents must be least-privilege and attributable.
11. **Saved Kanban/calendar everywhere** — saved worklists may be useful; generic CRM presentation modes are not.
12. **Dynamic physical DDL per tenant** — unnecessary migration/locking/backup complexity.

## Where InvoiceIQ is already better for its use case

- Financial domain state is represented as typed models and explicit transitions, not generic object metadata.
- Money/tax/payment paths are protected by domain-specific constraints and concurrency tests.
- Tenant isolation is enforced three ways including PostgreSQL FORCE RLS.
- Background work has a durable PostgreSQL queue without Redis operational dependency.
- Capture provenance and AI use are advisory/human-confirmed.
- Automations are bounded, versioned, dry-runnable and lack embedded scripting.
- OpenAPI is checked in and CI-gated.
- Frontend query error handling has a structural ratchet.
- Real PostgreSQL RLS/concurrency/performance shape tests are part of CI.

## Cross-repository learning: Scrapling + Paperless + Twenty

### Outbound integrations
- Scrapling: respect downstream `Retry-After`.
- Paperless: connect to the DNS result that passed SSRF validation.
- Twenty: enforce outbound safety at the connection layer, including redirect connections.
- InvoiceIQ: HMAC + durable job + idempotency + retry/DLQ.

**Resulting InvoiceIQ architecture:** signed durable delivery + connect-time network policy + receiver-directed backoff.

### Async work
- Scrapling: process-local scheduler/checkpoints.
- Paperless: Celery/Redis.
- Twenty: BullMQ/Redis behind a driver.
- InvoiceIQ: PostgreSQL queue.

**Decision:** keep PostgreSQL queue; adopt operational principles only.

### Extensibility
- Paperless: parser plugin entrypoints.
- Twenty: full application metadata/functions/front-components.
- InvoiceIQ: fixed modules/providers and bounded automation.

**Decision:** current explicit model wins. Future extensibility must be declarative or strongly isolated.

### AI authorization
- Scrapling/Paperless: AI is primarily data-processing/advisory.
- Twenty: AI agents are actors with roles, tool catalogs, usage budgets and telemetry.
- InvoiceIQ: current AI extraction/review is advisory; transport MCP concept is read-only.

**Decision:** if InvoiceIQ crosses from advisory/read-only AI into write/action tools, adopt a role-intersection + actor-attribution + tool-allowlist + spend/step-budget architecture before enabling writes.

### UX action discovery
- Twenty: Cmd/Ctrl+K contextual command menu.
- InvoiceIQ: grouped permission-aware sidebar.

**Decision:** add a lightweight palette over the existing filtered navigation; do not build an action metadata platform.

## Engineering debates

### TWENTY-D1 — Replace typed finance models with metadata objects
**Support:** faster customization, app ecosystem.
**Opposition:** tax/payment/accounting semantics need stable typed invariants; dynamic schema multiplies migration/permission complexity.
**Cost:** VERY HIGH  
**Risk:** VERY HIGH  
**Expected benefit:** LOW for current ICP  
**Lead:** REJECT.

### TWENTY-D2 — Adopt schema-per-workspace
**Support:** physical isolation and arbitrary per-tenant schema.
**Opposition:** InvoiceIQ already has tested RLS and a shared product schema; per-tenant DDL complicates every migration and backup.
**Lead:** REJECT.

### TWENTY-D3 — Add command palette
**Support:** low-cost discoverability improvement; uses existing filtered navigation so permissions/modules remain correct automatically.
**Opposition:** a palette can become a second navigation architecture if it has independent configuration.
**Alternative:** derive entirely from the same `navGroups`.
**Cost:** LOW  
**Risk:** LOW  
**Benefit:** MEDIUM/HIGH UX  
**Lead:** ADAPT NOW.

### TWENTY-D4 — Generic migration plan engine
**Support:** preview destructive schema/config operations before mutation.
**Opposition:** Alembic + current preflights already protect real production data; building a metadata planner would duplicate migration infrastructure.
**Alternative:** migration review policy/checklist + explicit destructive preflight.
**Lead:** ADAPT PRINCIPLE, REJECT ENGINE.

### TWENTY-D5 — Semantic API breaking-change gate
**Support:** external integrations should not be silently broken.
**Opposition:** snapshot drift already detects every contract change; semantic tooling adds maintenance/dependency cost.
**Lead:** DEFER P3 until public API compatibility is contractual.

### TWENTY-D6 — Write-capable AI agents
**Support:** automation/productivity.
**Opposition:** current product has no demonstrated need for autonomous write tools; finance actions carry payment/tax consequences.
**Lead:** DEFER. Before first write tool, implement agent identity, role intersection, explicit tool allowlists, audit actor, max steps and spend caps.

### TWENTY-D7 — Saved views
**Support:** finance operators repeat filters/worklists daily.
**Opposition:** generic saved-view infrastructure is product complexity without usage evidence.
**Lead:** P3 discovery. Start only when repeated workflows are measured.

## Prioritized backlog

| ID | Priority | Reference lesson | Proposed InvoiceIQ adaptation | Complexity | Risk | Status |
|---|---|---|---|---|---|---|
| TW-P2-01 | P2 | Context-aware command menu | Cmd/Ctrl+K navigation palette derived from filtered `navGroups` | S | L | PATCH PREPARED |
| TW-P2-02 | P2 | Agent least privilege | Architecture contract for any future write-capable AI/MCP | S docs / L runtime | L now | DOCUMENTED |
| TW-P2-03 | P2 | Plan-before-apply | Migration safety policy: explicit destructive impact + fail-closed preflight | S | L | DOCUMENTED |
| TW-P3-01 | P3 | Semantic API diff | Compare current OpenAPI against base for breaking changes | M | L/M | DEFER |
| TW-P3-02 | P3 | Saved views | Validate saved worklists on invoices/claims before implementation | M | L | DISCOVERY |
| TW-P3-03 | P3 | Domain/tool metrics | Add metrics where queue/integration/AI telemetry proves useful | S/M | L | INCREMENTAL |
| TW-P4-01 | P4 | Isolated extension UI | Security rule for future tenant apps/plugins | L | H | FUTURE |
| TW-P4-02 | P4 | Tool catalog lazy loading | Future agent optimization after tool-count measurement | M | M | FUTURE |

No new P0/P1 application defect was discovered from Twenty. Existing P1 repository governance from the Paperless cycle (unprotected `main`) remains open and is reinforced by Twenty's protected main.

## Lead Developer implementation order — TW-P2-01

**Objective**  
Make the growing InvoiceIQ application easier to navigate without inventing a global-search backend or a second authorization model.

**Reference lesson**  
Twenty's command menu centralizes discovery and derives availability from current context/permissions.

**Current implementation**  
`Layout.tsx` already computes module/permission-filtered `navGroups`; `AppShell` renders exactly those destinations. No global search endpoint exists.

**Target architecture**
```text
served permissions + enabled modules
            ↓
        filterNav()
            ↓
         navGroups
        ↙        ↘
 sidebar       Quick navigation
                  ↓
             route navigation
```

**Files/components**
- `frontend/src/components/shell/CommandPalette.tsx` — new
- `frontend/src/components/shell/AppShell.tsx`
- `frontend/src/components/Layout.tsx`
- `frontend/e2e/nav.spec.ts`

**Implementation**
1. Add `CommandPalette` using the existing accessible `Modal`.
2. Listen for Ctrl+K / Meta+K.
3. Search only the caller-supplied, already-filtered `navGroups`.
4. Match label/group/path locally.
5. Support keyboard Up/Down/Enter/Escape.
6. Expose a visible “Quick find” trigger.
7. Enable only in the live non-suspended shell.
8. Do not add any API request or dependency.

**Do not change**
- backend permissions;
- nav filtering;
- module gating;
- routes;
- sidebar;
- global-search prop;
- mobile drawer;
- design showcase behavior.

**Tests required**
- owner opens with Ctrl+K and navigates to Invoices;
- employee cannot discover Upload;
- disabled module destinations remain absent because palette uses filtered nav;
- existing nav E2E remains green;
- TypeScript build and visual-regression suite remain green.

**Acceptance**
- one source of truth for destination availability;
- no inaccessible destination appears;
- no new backend/search service;
- keyboard navigation works;
- accessibility uses existing focus-trapped Modal.

**Rollback**
Delete the new component, remove the AppShell prop/render and Layout flag; no data/schema changes exist.

## Architecture law for future AI — TW-P2-02

A write-capable InvoiceIQ AI/MCP tool must not ship until all are true:

1. Every agent execution has `org_id`, actor identity and immutable audit attribution.
2. Agent has its own role/capability set.
3. If it “runs as” a user, effective permission is the safe intersection/composition of agent and user permissions; never escalation by union.
4. Tool catalog is explicit and default-deny.
5. Financial side-effect tools are separately classified from read tools.
6. Recursive workflow/agent invocation has an explicit depth/step guard.
7. Max model/tool steps are enforced server-side.
8. Per-tenant/user spend/usage ceilings are enforced before and during execution.
9. Every consequential tool call is idempotent or requires a durable idempotency key.
10. Tool latency, errors, model usage and cost are measurable.
11. Human approval remains required for payment/tax submission unless the product owner deliberately changes that policy with an audited risk review.

## Migration law — TW-P2-03

For every migration that can delete data, narrow accepted values, change FK delete behavior, make a column non-null, or reinterpret financial state:

1. State the destructive/compatibility effect in the migration docstring/work order.
2. Query production-shaped data BEFORE DDL.
3. If ambiguous offending rows exist, refuse with table/key/count; never normalize by guess.
4. Normalization is allowed only where semantic equivalence is exact and documented.
5. Test the refusal path, not only the clean path.
6. Run on real PostgreSQL in CI.
7. Include rollback/repair instructions.
8. Serialize production migration execution.
9. Never edit a migration already shipped.
10. Treat AI-generated migration changes exactly like human-generated ones: preview/review before apply.

InvoiceIQ's `c4d6e8f0a2b4` is the positive reference implementation for this rule.

## Analysis status

Architecture understanding (transferable areas): **~93%**  
Patterns identified: **25**  
High-transfer principles: **13**  
Immediate code adoption: **1**  
Cross-repository reinforcement: **5**  
Rejected/cargo-cult patterns: **12**  
P0 discovered: **0**  
P1 new discovered: **0**  
P2: **3**  
P3: **3**  
P4: **2**

Repository write status: expected BLOCKED from existing GitHub integration permission; branch delivery will be attempted once after the patch is prepared.


---

# Repository 4 — lissy93/personal-security-checklist

**Repository:** `lissy93/personal-security-checklist`  
**URL:** https://github.com/lissy93/personal-security-checklist  
**Reference commit:** `5daa89e74762be7418cd2042e7d82ec738ecb701` (`master`)  
**Analysis date:** 2026-09-06  
**Our system/base:** `kristapsgoncoronoks-ship-it/Bid_it` at `584fb21d4f95ab49f214d592c651252ca645fa1a`

## Executive conclusion

This repository is valuable to InvoiceIQ primarily as a **security-governance and supply-chain reference**, not as an application-architecture reference.

It does four things particularly well:

1. turns security guidance into a structured, prioritized, trackable control set;
2. separates a canonical machine-readable source from generated human-readable output;
3. treats repository automation itself as part of the attack surface;
4. requires evidence/disclosure for security-content changes.

It is not a model to copy wholesale. Its runtime is a mostly static Qwik site, user progress is stored in browser localStorage, there is no financial backend or tenant database, its default branch is not protected, its README describes an API whose visible implementation is incomplete, its Dockerfile is empty, and the web package exposes lint/type/build scripts but no test script.

The Lead Developer decision is therefore:

- **ADOPT** immutable GitHub Action pins and dedicated supply-chain CI.
- **ADOPT** a repository-visible, machine-readable security-control register.
- **ADOPT** evidence/risk/AI-disclosure fields in pull requests.
- **FIX** InvoiceIQ's placeholder `SECURITY.md`, but only after the owner chooses a real private reporting channel.
- **REINFORCE** the existing MFA/step-up and session-hardening P1.
- **REJECT** localStorage as security assurance, self-mutating generated docs on `main`, static-site architecture for the SaaS core, and this reference repository's own unprotected-default-branch practice.

## 1. Repository purpose

The project is a curated personal digital-security and privacy checklist plus a public website (`digital-defense.io`) for browsing, filtering, tracking completion, and visualizing progress.

The canonical checklist is stored in `personal-security-checklist.yml`. A Python generator turns that data into the human-readable `CHECKLIST.md`. The Qwik/TypeScript site consumes the same checklist data and stores a visitor's progress locally in the browser.

This is best understood as a **security knowledge product**:

```text
security research / contributor evidence
              |
              v
personal-security-checklist.yml
       (canonical content)
        /             \
       v               v
generated Markdown    Qwik website
CHECKLIST.md           |
                       +--> filter/sort/priorities
                       +--> local completion state
                       +--> charts/progress
```

It is not a backend security platform and has no comparable multi-tenant financial data model.

## 2. Architecture map

```text
                      +-----------------------+
                      | contributor / editor  |
                      +-----------+-----------+
                                  |
                                  v
                  personal-security-checklist.yml
                         canonical dataset
                           /             \
                          /               \
                         v                 v
                lib/generate.py       Qwik / Qwik City
                         |                 |
                         v                 v
                    CHECKLIST.md       static/SSR web app
                                           |
                                           v
                                      browser localStorage
                                      progress / ignored
```

Supporting engineering infrastructure:

```text
GitHub PR
  |
  +--> format
  +--> ESLint
  +--> TypeScript
  +--> static build + output verification
  +--> actionlint
  +--> zizmor
  +--> TruffleHog
  +--> dependency-review
  |
  v
review / merge
```

## 3. Execution flow

### Checklist content flow

```text
AUTHOR
  ↓
edit canonical YAML
  ↓
review evidence / PR
  ↓
generator
  ↓
CHECKLIST.md
  ↓
website build
  ↓
static pages
```

### User-progress flow

```text
USER
  ↓
Qwik checklist UI
  ↓
mark done / ignored
  ↓
browser localStorage
  ↓
priority + section calculations
  ↓
progress bars / charts / filters
```

There is no server-side account or progress database for this workflow.

## 4. Top engineering ideas

### PSC-001 — Security controls as prioritized, actionable records
**Category:** Security / Governance / UX  
**What:** controls have a title, priority and explanatory rationale; users can see what remains and what is completed.  
**Problem solved:** a flat security document does not tell operators what to do first or whether a control is actually implemented.  
**Reference implementation:** `personal-security-checklist.yml`, checklist table and progress components.  
**Advantages:** prioritization, visibility, measurable progress.  
**Weaknesses:** personal-security priorities are not a SaaS risk model; browser state is not evidence.  
**Transferability:** **VERY HIGH principle**.  
**InvoiceIQ adaptation:** repository-controlled security register with stable IDs, owner, status, evidence and blocker/rationale.

### PSC-002 — Canonical machine-readable source + generated human view
**Category:** Architecture / Documentation  
**What:** YAML is source-of-truth; Markdown is derived.  
**Problem solved:** human docs and structured data otherwise drift.  
**Reference implementation:** `personal-security-checklist.yml` → `lib/generate.py` → `CHECKLIST.md`.  
**Transferability:** **HIGH**.  
**InvoiceIQ adaptation:** JSON security-control register → deterministic Markdown; CI fails on drift. Do **not** let CI auto-commit it.

### PSC-003 — Explicit priority vocabulary
**Category:** Security / Product  
**What:** Essential / Optional / Advanced.  
**Problem solved:** security advice has unequal risk reduction.  
**Transferability:** **HIGH principle**.  
**InvoiceIQ adaptation:** keep our existing P0–P4 engineering priority vocabulary rather than copying the labels.

### PSC-004 — “Ignored / not applicable” is distinct from “done”
**Category:** Security / Governance  
**What:** progress calculations exclude ignored items while preserving the distinction.  
**Problem solved:** a control may legitimately not apply, but silently calling it implemented destroys assurance.  
**Transferability:** **HIGH**.  
**InvoiceIQ adaptation:** `not_applicable` requires rationale; `verified` requires evidence; `blocked` requires blocker.

### PSC-005 — Security advice requires supporting material
**Category:** Governance / Code review  
**What:** PR/issue templates request sources/evidence for additions or amendments.  
**Problem solved:** plausible-sounding security advice can be wrong, stale or cargo-culted.  
**Transferability:** **VERY HIGH**.  
**InvoiceIQ adaptation:** PR template requires evidence, tests, security/data/financial impact and rollback.

### PSC-006 — AI contribution disclosure
**Category:** Governance / AI  
**What:** PR template asks contributors to disclose whether AI was used.  
**Problem solved:** reviewers can increase scrutiny for generated changes and verify claims/tests independently.  
**Transferability:** **HIGH**.  
**InvoiceIQ adaptation:** disclosure does not lower quality standards; AI-generated migrations/security changes get the same review and test obligations.

### PSC-007 — Full-SHA pinning of GitHub Actions
**Category:** Supply chain / DevOps  
**What:** external actions are pinned to immutable commit SHAs with human-readable version comments.  
**Problem solved:** mutable tags can be repointed after review.  
**Transferability:** **VERY HIGH / immediate**.  
**InvoiceIQ evidence:** current CI/release still uses mutable major tags.  
**Decision:** **ADOPT P1**.

### PSC-008 — Checkout credentials disabled by default
**Category:** Supply chain  
**What:** CI checkouts use `persist-credentials: false` except workflows that intentionally write.  
**Problem solved:** later steps/actions do not need an unnecessary persisted repository credential.  
**Transferability:** **HIGH**.  
**InvoiceIQ adaptation:** false everywhere except the deliberately write-capable VR-baseline branch updater or other reviewed write workflows.

### PSC-009 — Minimum workflow permissions
**Category:** Supply chain  
**What:** read-only top-level workflow permissions, widened only when needed.  
**Problem solved:** action compromise blast radius.  
**Transferability:** **VERY HIGH**.  
**InvoiceIQ:** current release already scopes permissions; current CI should make read-only default explicit and preserve reviewed per-job write overrides.

### PSC-010 — Workflow source is linted as security-sensitive code
**Category:** Testing / DevOps / Security  
**What:** actionlint checks syntax/semantics; zizmor checks GitHub Actions security footguns.  
**Problem solved:** workflows can leak secrets or grant unsafe permissions even when application tests are green.  
**Transferability:** **VERY HIGH**.  
**Decision:** **ADOPT P1/P2 supply-chain gate**.

### PSC-011 — General secret scanning is separate from PII scanning
**Category:** Security  
**What:** TruffleHog scans PR history/diff for credentials.  
**Problem solved:** API keys/tokens/cloud credentials are not the same thing as customer PII.  
**Transferability:** **VERY HIGH**.  
**InvoiceIQ evidence:** current PII gate is excellent but intentionally looks for Fleet Fuel identifiers, VAT IDs and IBANs, not generic credentials.  
**Decision:** **ADOPT** generic secret scanning in addition to PII quarantine.

### PSC-012 — Dependency changes receive a dedicated vulnerability review
**Category:** Supply chain  
**What:** dependency-review action fails at moderate severity.  
**Problem solved:** package changes can introduce known vulnerabilities before language-specific scanners run.  
**Transferability:** **HIGH**.  
**InvoiceIQ:** pip-audit already covers Python; add PR-level dependency review across repository ecosystems.

### PSC-013 — Dependency updates are grouped and rate-limited
**Category:** Maintainability / Supply chain  
**What:** Dependabot groups ecosystems, limits open PRs and applies cooldowns.  
**Problem solved:** security maintenance cannot work if update PR noise becomes unreviewable.  
**Transferability:** **MEDIUM**.  
**InvoiceIQ:** already has strong weekly grouped Dependabot; keep our faster financial-SaaS cadence rather than copying monthly updates.

### PSC-014 — CI validates the actual built artifact
**Category:** Testing  
**What:** after SSG build, expected files and sitemap/page counts are checked.  
**Problem solved:** “build exited 0” is weaker than “the deployment artifact contains what the product needs.”  
**Transferability:** **HIGH principle**.  
**InvoiceIQ:** already goes further through container builds, E2E, visual regression and deploy health; no new architecture needed.

### PSC-015 — Path-aware CI
**Category:** DevOps / Performance  
**What:** unrelated checks can be skipped based on changed paths.  
**Problem solved:** CI cost/latency in multi-area repos.  
**Transferability:** **LOW/MEDIUM now**.  
**InvoiceIQ:** broad cross-layer gates catch drift and CI duration is not the highest-priority issue. Defer until measured.

### PSC-016 — Local-first state minimizes collected data
**Category:** Privacy / UX  
**What:** user checklist progress lives only in browser storage.  
**Problem solved:** the service need not collect progress/identity it does not require.  
**Transferability:** **HIGH principle, LOW implementation**.  
**InvoiceIQ:** minimize telemetry/profile data, but regulated financial state must remain durable/audited server-side.

### PSC-017 — User-controlled deletion of local progress
**Category:** Privacy / UX  
**What:** UI exposes deletion of local data.  
**Transferability:** **MEDIUM principle**.  
**InvoiceIQ:** already has substantially stronger GDPR erasure preview/execute with statutory-retention/legal-hold classification.

### PSC-018 — Security progress visualization
**Category:** Security / UX  
**What:** controls can be filtered, sorted and visualized by priority/section.  
**Transferability:** **MEDIUM/HIGH for internal engineering**.  
**InvoiceIQ:** generated security-control Markdown should show counts by P0–P4/status; do not build customer-facing charts yet.

### PSC-019 — Keep contribution surface explicit
**Category:** Governance  
**What:** structured issue types for add/amend/remove and PR self-review.  
**Transferability:** **MEDIUM/HIGH**.  
**InvoiceIQ:** use a focused PR template rather than numerous issue forms until contribution volume demands them.

### PSC-020 — Static delivery dramatically reduces runtime attack surface
**Category:** Architecture / Security  
**What:** most functionality is pre-rendered/static with browser-local progress.  
**Transferability:** **LOW to InvoiceIQ core; HIGH to public docs/marketing**.  
**Decision:** use static hosting for content surfaces where practical, not for transactional finance.

### PSC-021 — Generated data must have stable identity
**Category:** Data / UX  
**Reference issue:** checklist progress IDs are derived from display text, so renaming a point can orphan progress.  
**Transferability:** **HIGH negative lesson**.  
**InvoiceIQ:** security controls get immutable IDs independent of labels.

### PSC-022 — Repository security posture is not guaranteed by repository topic
**Category:** Adversarial review  
**Reference evidence:** despite being a security project, default `master` is unprotected.  
**Transferability:** **HIGH negative lesson**.  
**InvoiceIQ:** never infer governance quality from popularity or subject matter; our existing branch-protection P1 remains.

### PSC-023 — Documentation claims must be executable
**Category:** Code quality / Governance  
**Reference issue:** README describes API endpoints, while visible `lib/api.py` / `api-spec.yml` are empty and current route tree does not expose those API paths.  
**Transferability:** **HIGH negative lesson**.  
**InvoiceIQ:** continue contract/drift tests; do not let roadmap text masquerade as shipped capability.

### PSC-024 — Build/lint/typecheck are not a substitute for tests
**Category:** QA  
**Reference issue:** web package has no test script despite good format/lint/type/build CI.  
**Transferability:** **HIGH negative lesson**.  
**InvoiceIQ:** keep our substantially stronger backend/Postgres/E2E/VR/regression test strategy.

### PSC-025 — Security policy is part of product readiness
**Category:** Security governance  
**Reference lesson:** security-conscious open projects make contribution/reporting expectations visible.  
**InvoiceIQ evidence:** current `SECURITY.md` is still placeholder boilerplate with fictional supported versions and no reporting route.  
**Transferability:** **VERY HIGH**.  
**Decision:** **P1 BLOCKED on owner reporting-channel decision**.

## 5. Patterns we should not adopt

1. **Browser localStorage as authoritative security-control evidence.**
   - Fine for anonymous checklist progress.
   - Wrong for production assurance or auditable financial controls.

2. **Title-derived control IDs.**
   - A wording change must never change control identity.

3. **Auto-committing generated output to the default branch.**
   - The reference's checklist-generation workflow writes generated Markdown back to `master`.
   - InvoiceIQ should use the stronger existing OpenAPI-style pattern: regenerate in review, CI fails if derived output drifts.

4. **Unprotected default branch.**
   - The reference itself reports `master` unprotected.
   - Security topic/reputation is not evidence of repository governance.

5. **Static Qwik architecture for the transactional SaaS.**
   - It solves a content-site problem, not multi-tenant finance.

6. **No test suite because the site is mostly static.**
   - InvoiceIQ's financial invariants demand backend/Postgres/E2E/concurrency tests.

7. **Copying all personal-security tips into enterprise controls.**
   - Personal-device/browser advice is not automatically relevant to server-side SaaS risk.

8. **Monthly dependency updates by imitation.**
   - InvoiceIQ's weekly cadence is more appropriate for a live financial system and already controls PR volume.

9. **Incomplete API/Docker placeholders.**
   - Do not create placeholder architecture surfaces that documentation can accidentally market as real.

10. **Self-hosting or adding a security-dashboard product purely because the checklist has charts.**
    - A generated repository table is enough initially.

11. **Treating “ignored” as unrecorded.**
    - Not-applicable requires explicit rationale and review.

12. **Treating AI disclosure as a substitute for code review.**
    - AI-assisted code receives *more explicit evidence requirements*, not less.

## 6. Comparative architecture

### CMP-PSC-001 — Security-control tracking

**REFERENCE:** prioritized checklist; complete/ignored progress; local browser state.  
**OUR SYSTEM:** extensive audit/backlog docs, but no compact canonical security-control register.  
**PROBLEM:** evidence exists across code/tests/audit docs, but current posture is difficult to answer from one machine-readable source.  
**REFERENCE ADVANTAGE:** simple progress model.  
**OUR ADVANTAGE:** far stronger technical enforcement and tests.  
**RECOMMENDATION:** **ADAPT** — repository JSON + generated Markdown + CI drift gate.  
**CONFIDENCE:** HIGH.

### CMP-PSC-002 — GitHub Actions supply chain

**REFERENCE:** full-SHA pins, least token permissions, no persisted credentials, actionlint, zizmor.  
**OUR SYSTEM:** sophisticated CI but actions still referenced by movable major tags.  
**PROBLEM:** exact code executed by CI/release can change without a repo diff if a tag moves.  
**RECOMMENDATION:** **ADOPT** immutable SHAs; add workflow-security gate.  
**CONFIDENCE:** HIGH.

### CMP-PSC-003 — Secrets vs PII

**REFERENCE:** TruffleHog generic credential scan.  
**OUR SYSTEM:** strong custom PII scanner and nightly history scan.  
**PROBLEM:** API/token/cloud credentials are outside the scanner's declared scope.  
**RECOMMENDATION:** **HYBRID** — keep PII scanner and add generic credential scanning.  
**CONFIDENCE:** HIGH.

### CMP-PSC-004 — Dependency security

**REFERENCE:** dependency-review on PR + Dependabot.  
**OUR SYSTEM:** pip-audit + npm CI/audit signal + weekly Dependabot.  
**REFERENCE ADVANTAGE:** review specifically blocks dependency-introduction risk across ecosystems.  
**OUR ADVANTAGE:** stronger runtime/backend-specific audit and faster update cadence.  
**RECOMMENDATION:** **HYBRID** — add dependency-review; keep existing scanners/cadence.  
**CONFIDENCE:** HIGH.

### CMP-PSC-005 — Vulnerability disclosure

**REFERENCE:** mature contribution/security culture.  
**OUR SYSTEM:** `SECURITY.md` is untouched template text with fake version rows and no real report route.  
**PROBLEM:** external researcher cannot reliably report privately.  
**RECOMMENDATION:** **FIX P1**, after owner selects/activates the private reporting channel.  
**CONFIDENCE:** HIGH.

### CMP-PSC-006 — Authentication guidance

**REFERENCE:** marks MFA as essential and passkeys/security keys as preferred advanced/recommended options.  
**OUR SYSTEM:** revocable server-side sessions, lockout and session management are strong, but native MFA/step-up remains absent.  
**RECOMMENDATION:** **REINFORCE existing P1**; prefer WebAuthn/passkeys with recovery and step-up for privileged financial actions.  
**CONFIDENCE:** HIGH.

### CMP-PSC-007 — Privacy state

**REFERENCE:** anonymous progress stays local and can be cleared.  
**OUR SYSTEM:** financial/personal data is server-side because product function requires it; GDPR erasure preview + execute exists with retention/legal-hold reasons.  
**RECOMMENDATION:** **KEEP OUR APPROACH**; apply data minimization only to optional telemetry/profile features.  
**CONFIDENCE:** HIGH.

### CMP-PSC-008 — Documentation generation

**REFERENCE:** YAML generates Markdown, then workflow commits result directly to default branch.  
**OUR SYSTEM:** OpenAPI is generated but CI checks drift instead of silently blessing it.  
**RECOMMENDATION:** **HYBRID** — copy canonical-source principle, keep our fail-on-drift governance.  
**CONFIDENCE:** HIGH.

## 7. Engineering debates

### PSC-D1 — Add a canonical security-control register

**PROPOSAL:** `docs/security/security-controls.json` + deterministic Markdown + CI gate.

**SUPPORT:** current audit evidence is distributed; a compact control ledger makes OPEN / BLOCKED / VERIFIED explicit and prevents “we thought that was done.”

**OPPOSITION:** duplicate documentation can become stale.

**ALTERNATIVES**
A. Keep audit Markdown only.  
B. Add a spreadsheet/dashboard.  
C. Canonical JSON with generated Markdown.

**COST:** LOW  
**RISK:** LOW  
**BENEFIT:** HIGH  

**LEAD DECISION:** **ADOPT C**. JSON is source of truth; Markdown is generated; CI checks drift. No new runtime service.

### PSC-D2 — Pin GitHub Actions

**PROPOSAL:** replace major tags with reviewed full commit SHAs.

**SUPPORT:** exact CI/release code becomes immutable; release workflow can publish packages.

**OPPOSITION:** SHAs are less readable and require update maintenance.

**ALTERNATIVE:** tag + trusted publisher.

**COST:** LOW  
**RISK:** LOW  
**BENEFIT:** HIGH  

**LEAD DECISION:** **ADOPT P1**. Keep version comments and Dependabot updates for readability/maintenance.

### PSC-D3 — Add TruffleHog when PII scanner already exists

**SUPPORT:** PII and credentials are different secret classes.

**OPPOSITION:** another CI dependency and potential false positives.

**ALTERNATIVES:** GitHub secret scanning only; local regexes; TruffleHog PR diff.

**LEAD DECISION:** **ADOPT**, initially PR-diff verified-secret scanning. Keep the PII scanner unchanged.

### PSC-D4 — Add actionlint + zizmor

**SUPPORT:** workflows can be the path to repository/package credentials.

**OPPOSITION:** extra CI time and third-party dependencies.

**LEAD DECISION:** **ADOPT** in a small dedicated supply-chain workflow. Both actions themselves must be SHA-pinned.

### PSC-D5 — Copy reference dependency cadence

**SUPPORT:** cooldowns reduce churn.

**OPPOSITION:** InvoiceIQ is a live financial product; slower security-update uptake is undesirable.

**LEAD DECISION:** **REJECT cadence copy**. Keep weekly grouped Dependabot; adopt only immutable action pins and dependency review.

### PSC-D6 — Immediately replace SECURITY.md

**SUPPORT:** current file is actively misleading.

**OPPOSITION:** a reporting address/channel cannot be invented.

**LEAD DECISION:** **BLOCK owner decision**. Prepare draft/decision document now; merge only after a real private reporting mechanism is enabled and tested.

### PSC-D7 — Use the checklist's MFA recommendations as our implementation spec

**SUPPORT:** reference correctly elevates 2FA/passkeys.

**OPPOSITION:** consumer-security guidance is not an implementation standard.

**LEAD DECISION:** **REJECT as spec / ACCEPT as reinforcement**. Implement from current OWASP/NIST/WebAuthn guidance and our financial threat model.

## 8. Prioritized backlog

| ID | Priority | Problem | Adaptation | Complexity | Risk | Status |
|---|---:|---|---|---|---|---|
| SEC-SC-001 | P1 | Mutable GitHub Action tags | Pin every workflow action to full SHA + version comment | S | L | PATCH/HELPER READY |
| SEC-SC-002 | P1 | No generic credential scan | TruffleHog PR-diff gate in separate workflow | S | L/M | PROPOSED |
| SEC-GOV-001 | P1 | Placeholder `SECURITY.md` | Real private vulnerability report route + truthful support policy | S | L | BLOCKED OWNER |
| SEC-AUTH-001 | P1 | No native MFA/step-up | Existing P1 reinforced; WebAuthn/passkeys + recovery + sensitive-action step-up | L | M | EXISTING OPEN |
| SEC-SESS-001 | P1 | 24h session with no inactivity reauth | Add policy/inactivity or step-up semantics with MFA design | M | M | EXISTING/REINFORCED |
| OPS-GOV-001 | P1 | Default branch governance previously unprotected | Require PR + required checks + no force push/delete | S admin | L | EXISTING OPEN |
| SEC-SC-003 | P2 | Dependency introduction not semantically reviewed | dependency-review-action, fail moderate | S | L | PROPOSED |
| SEC-CTRL-001 | P2 | No compact canonical security posture | JSON control register + generated Markdown + drift gate | S | L | IMPLEMENTED IN BUNDLE |
| ENG-GOV-001 | P2 | No PR template | Risk/evidence/test/rollback/AI disclosure template | S | L | PROPOSED |
| SEC-KEK-001 | P2 | Independent production KEK not enforced | Provision separate KEK then enforce | M | M | EXISTING OPEN |
| SEC-PORTAL-001 | P2 | Long-lived plaintext portal capability token | Product decision + hashed/expiring token design | M | M | EXISTING OPEN |
| SEC-SSRF-001 | P3 | DNS/connection TOCTOU residual | Connect-time validated/pinned destination | M | M | EXISTING PATCH READY |
| CI-PATH-001 | P4 | CI can be expensive | Path-aware jobs only after measurement | M | M | DEFER |

No new P0 was discovered.

## 9. Lead Developer implementation orders

### TASK SEC-SC-001 — Immutable workflow dependencies

**Objective**  
Make the code executed by CI/release reviewable and immutable.

**Reference lesson**  
Full SHA pins with version comments.

**Current implementation**  
Major tags (`@v7`, `@v4`, etc.) in CI, PII history and release workflows.

**Target**
Every external action reference:
```yaml
uses: owner/action@<40-char-sha> # vX.Y.Z or vX
```

**Files**
- `.github/workflows/ci.yml`
- `.github/workflows/pii-history.yml`
- `.github/workflows/release.yml`

**Steps**
1. Resolve current trusted tag to repository SHA.
2. Replace the tag with SHA.
3. Preserve readable version comment.
4. Add `persist-credentials: false` to read-only checkout steps.
5. Preserve credentials only in intentionally write-capable jobs and document why.
6. Let Dependabot update action SHAs through PRs.

**Do not change**
- test matrix;
- deployment logic;
- container versions;
- branch baseline generation semantics;
- package-publish permissions.

**Tests**
- static scan finds zero mutable `uses:` major/version tags for external actions;
- actionlint;
- zizmor;
- full existing CI.

**Rollback**
Revert workflow-only commit.

### TASK SEC-SC-002/003 — Supply-chain security workflow

**Objective**
Protect repository credentials/dependencies/workflows independently of application tests.

**Target jobs**
- workflow review: actionlint + zizmor;
- secret scan: TruffleHog PR diff;
- dependency review: fail on moderate+ known vulnerabilities;
- security-control drift: stdlib gate.

**Do not remove**
- existing PII tree/history scanner;
- pip-audit;
- npm lockfile;
- Dependabot.

**Acceptance**
All tools pinned by SHA; workflow uses read-only token permissions by default.

### TASK SEC-CTRL-001 — Security-control register

**Objective**
Answer “what security controls exist, are they verified, what remains, and where is the evidence?” from one canonical file.

**Files**
- `docs/security/security-controls.json`
- `docs/security/SECURITY-CONTROLS.md` (generated)
- `scripts/security_control_gate.py`

**Rules**
- stable unique IDs;
- P0–P4;
- `verified | open | blocked | deferred | not_applicable`;
- verified requires evidence;
- blocked requires blocker;
- not-applicable requires rationale;
- P0 cannot remain open/blocked/deferred;
- generated Markdown must match canonical JSON.

**Do not**
- store this state in localStorage;
- key controls by display name;
- auto-commit generated Markdown from CI.

### TASK ENG-GOV-001 — Pull request evidence contract

**Objective**
Make security/data/financial impact explicit in every significant change.

**PR template asks for**
- objective/reason;
- security/data/financial/API impact;
- evidence/tests actually executed;
- migrations/rollback;
- new dependencies/external calls/secrets;
- AI assistance disclosure;
- unresolved owner decisions.

No checkbox may imply a test was run unless the author actually ran it.

### TASK SEC-GOV-001 — Vulnerability disclosure

**BLOCKED owner decision**

Before replacing `SECURITY.md`, choose and verify one private reporting path:
- enable GitHub Private Vulnerability Reporting, or
- provide a monitored security email/ticket channel.

Then replace template version rows with the actual support policy and response expectations. Never publish a fake address.

## 10. What InvoiceIQ already does better

InvoiceIQ should not regress toward the reference's much simpler runtime:

- real multi-tenant authorization and FORCE RLS;
- revocable server-side sessions;
- brute-force controls;
- response security headers, CSP and HSTS;
- safe download filename handling;
- GDPR erasure preview/execute with legal-hold/statutory retention;
- PII quarantine including entire Git history;
- pip-audit;
- real PostgreSQL migration/RLS/concurrency/performance tests;
- thousands of backend tests;
- Playwright functional and visual regression;
- typed financial domain constraints;
- bounded/versioned automation;
- durable jobs;
- audited money/tax workflows.

The reference is better at **repository supply-chain hygiene and compact control tracking**, not at the transactional application architecture.

## 11. Cross-repository library update

### Scrapling + Paperless + Twenty + PSC

**Outbound requests**
- Scrapling: receiver-directed retry (`Retry-After`).
- Paperless: bind the connection to the DNS result that passed SSRF validation.
- Twenty: enforce outbound network policy at the connection boundary.
- PSC: repository automation/dependencies are themselves supply-chain inputs.
- InvoiceIQ synthesis: signed/idempotent durable delivery + connect-time network policy + immutable build automation.

**AI**
- Twenty: agent identity, roles, tool allowlists, usage/step limits.
- PSC: disclose AI assistance in code/content review.
- InvoiceIQ synthesis: runtime least privilege + development-time provenance/evidence. AI does not get lower review standards.

**Security assurance**
- PSC: prioritized checklist and progress.
- InvoiceIQ: actual technical evidence/tests.
- Synthesis: stable control register whose `verified` state must cite repo evidence; no “checkbox security.”

**Generated artifacts**
- PSC: canonical YAML → generated Markdown, but auto-commits generated result to default branch.
- InvoiceIQ: OpenAPI drift gate.
- Synthesis: canonical structured source + deterministic generated view + **fail on drift**, never silently self-bless.

**Supply chain**
- PSC: strongest reference so far for GitHub Actions pins, workflow lint, secrets and dependency review.
- InvoiceIQ: adopt these around existing tests rather than replacing them.

## 12. Analysis status

Reference architecture understanding: **95%**  
Patterns identified: **25**  
Highly applicable: **12**  
Applicable with adaptation: **7**  
Rejected/negative lessons: **12**  
New P0: **0**  
New P1: **3** (`SEC-SC-001`, `SEC-SC-002`, `SEC-GOV-001`)  
Existing P1 reinforced: MFA/step-up, session policy, branch protection  
New P2: **3** (`SEC-SC-003`, `SEC-CTRL-001`, `ENG-GOV-001`)  
Implementation in this bundle: security-control register + generator/gate  
Proposed workflow/PR changes: prepared  
Repository delivery: to be attempted once after local artifact validation.

---

# Repository 5 — getlago/lago

**Repository:** `getlago/lago`  
**URL:** https://github.com/getlago/lago  
**Reference root commit:** `2453945562193f94b87812038fbdd52c5b1d1a41`  
**Pinned API submodule:** `getlago/lago-api@731388fca8d8b29d4c9a900d0f6325a509e8ab05`  
**Pinned frontend submodule:** `getlago/lago-front@dbde5273ec3f9c3c803845146ed0689fca16263a`  
**Analysis date:** 2026-09-06  
**InvoiceIQ source-of-truth:** `kristapsgoncoronoks-ship-it/Bid_it@525470a154d9f2b85941daca783f0a3499ec6526`

## Executive conclusion

Lago is the closest reference studied so far to InvoiceIQ's commercial billing boundary.
It is an API-first usage/metering/billing platform with:

- durable semantic event IDs;
- atomic batch event ingestion;
- explicit billing/subscription/invoice/payment/credit state;
- background workload isolation;
- provider-agnostic billing/payment/tax integrations;
- scheduled recovery scans;
- scenario-level billing tests;
- strong agent/developer contribution rules.

The most important result of the comparison is **not** "replace InvoiceIQ billing with Lago."
InvoiceIQ already has a stronger fit for its own accounting product in several areas:
tenant isolation, statutory issued-invoice lifecycle, append-only AR payment ledger,
payment-run maker/checker controls, durable DB jobs, and database-enforced financial state constraints.

The two highest-value Lago-derived findings are narrower:

1. **BILL-REL-001 — Stripe subscription webhook durability.**
   InvoiceIQ currently authenticates a Stripe event, applies it synchronously,
   catches *all* application failures, and still answers HTTP 200. A transient
   database/business failure can therefore lose a valid subscription/entitlement
   transition because Stripe has been told delivery succeeded.

2. **BILL-METER-001 — immutable outbound usage segments.**
   InvoiceIQ currently sends `count - reported`, but computes a new quantity and
   provider identifier from the latest counter every retry. If Stripe accepted an
   earlier meter event but its response was lost, and usage grows before retry,
   the retry can carry a new identifier and a larger quantity, causing over-reporting.
   The same lost-response failure class was already solved correctly in InvoiceIQ's
   EveryPay MIT renewal logic by persisting the semantic operation before the
   external financial side effect.

Lead decisions:

- **ADOPT / IMPLEMENT:** verified provider event → durable existing Postgres job → acknowledge.
- **ADOPT / IMPLEMENT:** persistent `reporting_target` watermark that freezes a meter segment before the Stripe call.
- **KEEP:** InvoiceIQ issued-invoice lifecycle and payment ledger.
- **KEEP:** InvoiceIQ Postgres job queue.
- **KEEP:** code-defined pricing plans until operator-editable product catalog becomes a proven product requirement.
- **DEFER:** per-kind job latency metrics.
- **REJECT:** Redis/Sidekiq/Kafka/ClickHouse/Meilisearch merely because Lago uses them.
- **REJECT:** copying Lago code; the repository is AGPL-3.0, and only engineering principles are transferred.

## 1. Repository purpose

Lago is monetization infrastructure. Its core flow is:

```text
USAGE / PRODUCT EVENT
        ↓
EVENT API
        ↓
METER / AGGREGATION
        ↓
PLAN / CHARGE MODEL
        ↓
CREDITS / TAXES / ENTITLEMENTS
        ↓
INVOICE
        ↓
PAYMENT / ACCOUNTING / WEBHOOKS
```

It intentionally separates metering/pricing from payment processors.

The root repository is orchestration/documentation plus pinned submodules:
`api/` and `front/`. The API is a Ruby on Rails application; background execution
uses Sidekiq/Redis, recurring scheduling uses Clockwork, and high-volume event paths
can use Kafka + ClickHouse.

## 2. Architecture map

```text
REST / SDK / product event
          |
          v
+-----------------------+
| Rails API             |
| auth / validation     |
| service objects       |
+-----------+-----------+
            |
            +-------------------------+
            |                         |
            v                         v
        PostgreSQL             event pipeline
    billing source of truth    Kafka / ClickHouse
            |                         |
            +------------+------------+
                         |
                         v
             billable metrics / fees
                         |
                         v
      plans / charges / credits / taxes
                         |
                         v
              invoice state machine
                         |
           +-------------+-------------+
           |                           |
           v                           v
       payments                 integrations/webhooks
           |
           v
      provider adapters

Sidekiq + Redis:
  default/high/low
  billing
  events
  payments
  pdf
  webhooks
  analytics
  alerts
  ai_agent
  wallets

Clockwork:
  bill/finalize/retry/dunning/cleanup/recovery scans
```

### Why this shape exists

Lago's workloads have very different resource profiles:
event ingestion is high-volume; invoice/PDF work is bursty; provider calls can block;
analytics and AI can be expensive. Queue routing is therefore configurable so these
classes can be isolated only when a deployment needs it.

InvoiceIQ already has the underlying principle through `jobs.claim(kinds/exclude)`.
It does not yet need separate queue infrastructure.

## 3. Execution flow — usage event

```text
POST /events
   ↓
signature/auth/API validation
   ↓
Events::CreateService
   ↓
construct semantic event
   ↓
evaluate expression
   ↓
INSERT event
  unique(org, external_subscription, transaction_id)
   ↓
Kafka publish where configured
   ↓
post-processing job
   ↓
aggregation / fees / billing
```

Batch input:

```text
batch
 ↓
validate ALL members
 ↓
bulk insert in transaction
 ↓
detect caller duplicates + DB duplicates
 ↓
if ANY error → rollback whole batch
 ↓
publish/process only accepted batch
```

The important principle is not Rails or Kafka. It is:

> A financial usage event has a caller-defined semantic identity, and a bulk command
> has explicit atomicity semantics.

## 4. Top engineering ideas

### LAGO-001 — Semantic event idempotency
**Category:** Backend / Reliability  
**Reference:** event `transaction_id`, DB unique `(organization, subscription, transaction_id)`.  
**Problem:** at-least-once producer/network retries.  
**Why it works:** the database—not process memory—is final authority.  
**Weakness:** callers must choose identity correctly.  
**Transferability:** HIGH.  
**InvoiceIQ:** already strongly used in jobs, recurring invoices, provider events and payment renewal. Keep and extend to every externally visible financial effect.

### LAGO-002 — Atomic batch event ingestion
**Category:** Backend / Data integrity  
**Reference:** validate all → `insert_all` → rollback on any member error.  
**Problem:** ambiguous half-accepted financial batches.  
**Transferability:** HIGH where InvoiceIQ exposes bulk imports/actions.  
**Decision:** principle adopted; audit each financial batch API rather than introducing a generic batch framework.

### LAGO-003 — Separate financial state dimensions
**Category:** Domain modeling  
**Reference:** invoice lifecycle, payment status and tax status are separate.  
**Problem:** one overloaded status becomes contradictory.  
**Transferability:** HIGH.  
**InvoiceIQ:** already does this better for AR: stored lifecycle + derived payment + separate delivery. KEEP.

### LAGO-004 — Explicit invoice finalization state machine
**Category:** Domain modeling  
**Reference:** draft/finalized/voided transitions.  
**Transferability:** HIGH principle.  
**InvoiceIQ:** already has draft → approved → issued + dispute/writeoff/cancel rules and DB CHECK. KEEP OUR APPROACH.

### LAGO-005 — Domain compatibility validations live close to billing models
**Category:** Correctness  
**Reference:** charge type/aggregation/pay-in-advance/proration combinations are rejected.  
**Problem:** invalid pricing configurations otherwise survive until invoice generation.  
**Transferability:** MEDIUM.  
**InvoiceIQ:** code-defined plans currently avoid most combinatorial configuration. Revisit only if editable pricing/catalog arrives.

### LAGO-006 — Provider-independent billing source of truth
**Category:** Architecture  
**Reference:** payment processors are downstream adapters, not pricing authority.  
**Transferability:** HIGH.  
**InvoiceIQ:** already follows this for internal usage and provider seams. KEEP.

### LAGO-007 — Durable inbound webhook receipt before acknowledgment
**Category:** Reliability  
**Reference:** verified inbound webhook → DB row → processing job; HTTP success does not depend on full business execution.  
**Problem:** transient processing errors after a 2xx lose at-least-once provider delivery.  
**Transferability:** VERY HIGH.  
**InvoiceIQ:** concrete Stripe gap. ADAPT using existing durable Job rows; no new inbound table.

### LAGO-008 — Scheduled recovery is independent from immediate retry
**Category:** Reliability  
**Reference:** periodic retry/recovery jobs re-drive stale/pending inbound work.  
**Transferability:** HIGH principle.  
**InvoiceIQ:** existing stale-lease reclaim + daily scheduler already provide infrastructure. No separate recovery framework required.

### LAGO-009 — Dedicated workload lanes are optional deployment topology
**Category:** Scalability  
**Reference:** billing/events/payments/PDF/webhook/analytics workers can be isolated by flags.  
**Transferability:** MEDIUM.  
**InvoiceIQ:** `kinds`/`exclude` already support lanes. Split processes only when queue metrics prove starvation/contention.

### LAGO-010 — Queue latency is a first-class SLO
**Category:** Observability  
**Reference:** per-queue oldest latency, depth, worker/busy counts and failure rates.  
**Transferability:** HIGH principle.  
**InvoiceIQ:** already has aggregate oldest-ready age, dead depth and SLO. Per-kind durations/failures are P3.

### LAGO-011 — Provider event processing is a service, not controller logic
**Category:** Code quality  
**Reference:** Create/Process services; controller verifies/shapes.  
**Transferability:** HIGH.  
**InvoiceIQ:** mostly aligned; current Stripe route contains processing policy that should move to durable service/job boundary.

### LAGO-012 — Shared result/error vocabulary
**Category:** Code quality / API  
**Reference:** BaseResult and typed failure classes.  
**Transferability:** MEDIUM.  
**InvoiceIQ:** already has AppError hierarchy + stable wire `code`; do not add a second result monad.

### LAGO-013 — Subscription transition lineage
**Category:** Domain model  
**Reference:** `previous_subscription`, next subscription, pending/active/terminated/canceled/incomplete.  
**Transferability:** LOW/MEDIUM.  
**InvoiceIQ:** provider subscription IDs/status are intentionally simpler. Do not build upgrade lineage unless product requirements need scheduled upgrades/downgrades beyond provider authority.

### LAGO-014 — Credit note state is distinct from refund state
**Category:** Financial correctness  
**Reference:** credit-note document state, available/consumed credit state, and refund state are independent.  
**Transferability:** HIGH principle.  
**InvoiceIQ:** credit note is a statutory correction and cash/payment corrections live in signed ledger rows. KEEP simpler current model.

### LAGO-015 — Payment attempts are explicit resources
**Category:** Reliability / Audit  
**Reference:** pending/processing/succeeded/failed provider payments, unique live provider payment, provider IDs.  
**Transferability:** HIGH.  
**InvoiceIQ:** EveryPay `BillingPayment`, AP runs and AR ledger already embody this. KEEP.

### LAGO-016 — Scenario tests span billing boundaries
**Category:** Testing  
**Reference:** dedicated `spec/scenarios` for billing, charge models, credit notes, current usage, analytics, events.  
**Transferability:** VERY HIGH.  
**InvoiceIQ:** already has broad acceptance tests; add explicit provider lost-response scenarios where narrow unit tests missed distributed-system failures.

### LAGO-017 — Migration schema drift is checked
**Category:** Database / CI  
**Reference:** run migrations, dump DB structure, compare, then ClickHouse migration.  
**Transferability:** HIGH principle.  
**InvoiceIQ:** Alembic single-head + upgrade + `alembic check` is already the native equivalent. KEEP.

### LAGO-018 — Deferred constraint validation carries a deadline
**Category:** Database migration safety  
**Reference agent rule:** NOT VALID constraints must be validated in a following migration or registered with a `validate_by` deadline.  
**Transferability:** MEDIUM/HIGH future.  
**InvoiceIQ:** no current NOT VALID debt found. Add to playbook; machine gate only when first such migration is needed.

### LAGO-019 — New models directly own tenant/org identity
**Category:** Tenant isolation  
**Reference agent rule:** new models directly belong to organization.  
**Transferability:** HIGH principle.  
**InvoiceIQ:** stronger: org_id registration + ORM guard + Postgres RLS + composite tenant FKs. KEEP OUR APPROACH.

### LAGO-020 — Developer/agent instructions encode repository invariants
**Category:** Engineering governance / Agents  
**Reference:** `AGENTS.md` prescribes service/job/controller/model/migration/test rules and exact execution commands.  
**Transferability:** VERY HIGH.  
**InvoiceIQ:** engineering-rules document already stronger on money/RLS. Cross-link rules into future agent operating contract rather than duplicating them.

### LAGO-021 — Progressively isolate scaling hotspots
**Category:** Performance  
**Reference:** a default worker can serve all queues until environment flags split specific workloads.  
**Transferability:** HIGH principle.  
**InvoiceIQ:** exactly the correct model for current scale; never pre-split infrastructure.

### LAGO-022 — Billing product catalog supports many pricing models
**Category:** Product / Architecture  
**Reference:** standard, graduated, volume, package, percentage, custom/dynamic, thresholds/commitments.  
**Transferability:** LOW currently.  
**InvoiceIQ:** commercial ladder is fixed/code-defined. A generalized rating engine is overengineering now.

### LAGO-023 — Customer-visible current usage
**Category:** UX / Product  
**Reference:** current usage and metering are first-class product surfaces.  
**Transferability:** MEDIUM/HIGH.  
**InvoiceIQ:** current plan/usage screen already exposes caps/remaining. If overage metering launches, show provider-independent internal count + billing cutoff/period.

### LAGO-024 — Observable provider failures, not silent fallbacks
**Category:** Reliability  
**Reference:** provider failure vocabulary, error details, retries.  
**Transferability:** HIGH.  
**InvoiceIQ:** current Stripe webhook violates this by converting processing exception to 200/no durable work. Fix BILL-REL-001.

### LAGO-025 — Root repo pins API and UI revisions
**Category:** Release architecture  
**Reference:** root Git submodules pin API/front commits.  
**Transferability:** LOW.  
**InvoiceIQ:** monorepo is an advantage: backend/frontend/contracts change atomically. Do not split repos.

## 5. Patterns we should not adopt

1. Redis/Sidekiq as a replacement for the current durable Postgres queue.
2. Kafka because Lago ingests billing events at Lago-scale.
3. ClickHouse for ordinary InvoiceIQ product analytics.
4. Meilisearch without measured database-search limits.
5. Lago's worker concurrency/memory numbers.
6. Separate repositories/submodules for backend/frontend.
7. The full product-catalog/rating engine.
8. Rails service/result conventions in Python.
9. AASM dependency for InvoiceIQ state machines.
10. A second inbound-webhook table where `jobs` already supplies durable work state.
11. Lago's current mutable GitHub Action tags; previous reference learning is stronger.
12. AGPL code copying. Extract principles and implement independently.
13. Large ActiveRecord models that accumulate many domain concerns.
14. Lago's exact queue taxonomy.
15. A generic reconciliation framework before concrete gaps exist.

## 6. Comparative findings

### CMP-LAGO-001 — Stripe inbound event reliability
**Reference:** authenticate → persist inbound receipt → async process → recovery.  
**InvoiceIQ:** authenticate → synchronously apply → catch all failures → HTTP 200.  
**Concrete problem:** valid provider event can be lost after transient application failure.  
**Reference advantage:** acknowledgment follows durability, not successful business execution.  
**InvoiceIQ advantage:** durable jobs already provide retries/leases/DLQ, so no new receipt table needed.  
**Recommendation:** **ADAPT**.  
**Confidence:** HIGH.

### CMP-LAGO-002 — Usage report idempotency
**Reference principle:** semantic transaction IDs identify immutable usage events.  
**InvoiceIQ:** deterministic ID is based on latest cumulative count; quantity is recomputed from mutable watermark.  
**Concrete problem:** accepted response lost + count growth creates a new ID and expanded delta.  
**Recommendation:** **ADAPT** by freezing `reporting_target` before external call.  
**Confidence:** HIGH.

### CMP-LAGO-003 — Invoice state
**Reference:** status/payment_status/tax_status are independent.  
**InvoiceIQ:** stored lifecycle + derived AR payment + delivery; DB closed-set constraint.  
**Recommendation:** **KEEP OUR APPROACH**.  
**Confidence:** HIGH.

### CMP-LAGO-004 — Payment history
**Reference:** explicit provider/manual Payment resources.  
**InvoiceIQ:** append-only signed AR payment ledger; negative corrections; composite tenant FK.  
**Recommendation:** **KEEP OUR APPROACH**.  
**Confidence:** HIGH.

### CMP-LAGO-005 — Work queues
**Reference:** Redis/Sidekiq + many optional dedicated queues.  
**InvoiceIQ:** Postgres jobs with leases, attempts, backoff, DLQ, stale recovery and lane filters.  
**Recommendation:** **KEEP**, split worker processes only based on measured queue SLO.  
**Confidence:** HIGH.

### CMP-LAGO-006 — Pricing catalog
**Reference:** highly configurable pricing engine.  
**InvoiceIQ:** fixed product ladder + provider price IDs + org quota matrix.  
**Recommendation:** **KEEP SIMPLE** until editable pricing is a validated product need.  
**Confidence:** HIGH.

### CMP-LAGO-007 — Observability
**Reference:** queue depth, latency and per-worker/job duration/failure metrics.  
**InvoiceIQ:** queue counts, DLQ, oldest-ready SLO.  
**Recommendation:** **HYBRID P3** — add kind/duration/failure only after queue incidents or lane split.  
**Confidence:** MEDIUM.

### CMP-LAGO-008 — Migration governance
**Reference:** migration structure check + NOT VALID deadline rule.  
**InvoiceIQ:** stronger runtime schema/RLS drift gates, forward-only additive rule.  
**Recommendation:** KEEP; add deadline pattern only if deferred validation is introduced.  
**Confidence:** HIGH.

## 7. Engineering debates

### LAGO-D1 — Add durable Stripe webhook receipt table?
**Support:** Lago has explicit inbound receipt state/reprocessing.  
**Opposition:** duplicates Job state already present in InvoiceIQ.  
**Alternatives:**  
A. new InboundBillingWebhook table;  
B. existing Job row keyed by provider event ID;  
C. keep synchronous route.  
**Cost:** B low, A medium.  
**Risk:** B low.  
**Benefit:** HIGH.  
**Lead decision:** **B — ADAPT**.

### LAGO-D2 — Return 200 after Stripe business-processing failure?
**Support for current behavior:** avoids provider retry storms for permanent business errors.  
**Opposition:** a transient DB error becomes permanent event loss.  
**Alternative:** return 200 only after durable enqueue; worker owns retry/DLQ.  
**Lead decision:** **CHANGE**. Signature failures remain 400; verified irrelevant/unknown events may 200/no-op; matched actionable event 200 only after durable job commit.

### LAGO-D3 — Add `reporting_target` to usage counter?
**Support:** freezes outbound unit of work before external side effect.  
**Opposition:** adds persistent state/migration.  
**Alternatives:** recompute delta (current); separate report ledger table; watermark target column.  
**Lead decision:** **ADAPT target column**. One column is enough at current scale; Job already records execution attempts.

### LAGO-D4 — New billing event table for metering?
**Decision:** **REJECT for now**. Internal counter is already authority; adding append-only upload events solely for Stripe would add storage without fixing a current internal metering problem.

### LAGO-D5 — Split billing worker process?
**Decision:** **DEFER**. `kinds` already enable it. No evidence queue starvation currently exists.

### LAGO-D6 — Adopt generalized rating engine?
**Decision:** **REJECT**. It solves Lago's product, not InvoiceIQ's current pricing ladder.

### LAGO-D7 — Add scenario tests
**Decision:** **ADOPT** for distributed billing boundaries. A lost-response test is more valuable than many model tests because it proves the exact failure mode.

## 8. Prioritized backlog

| ID | Priority | Problem | Adaptation | Complexity | Risk | Status |
|---|---:|---|---|---|---|---|
| BILL-REL-001 | P1 | Verified Stripe event can be acknowledged after failed business apply | Durable existing Job before 200 | M | L/M | PATCH READY |
| BILL-METER-001 | P1 before meter activation | Accepted meter response can be lost, then expanded retry can over-report | Persistent `reporting_target` + fixed segment | M | M | PATCH READY |
| BILL-QA-001 | P2 | No lost-response end-to-end billing scenario | Scenario regression tests around queue/provider/idempotency | S/M | L | INCLUDED |
| BILL-BATCH-001 | P2 | Bulk financial operations need explicit atomicity review | Audit/import-specific checks, not generic framework | M | L | PLANNED |
| JOB-OBS-001 | P3 | No per-kind duration/failure series | Add metrics only if operational need appears | S/M | L | DEFER |
| DB-MIG-VALIDATE-001 | P4 | Future NOT VALID constraints can become permanent debt | validate-by registry/gate if first one is introduced | S | L | FUTURE |
| BILL-CATALOG-001 | P4 | No dynamic pricing engine | Only if product requires operator-configurable pricing | XL | H | REJECT NOW |

`BILL-METER-001` is not P0 because repository deployment docs still list `STRIPE_METER_UPLOAD`
as owner-side go-live wiring; the feature is not proven active in production. It is a **hard precondition**
before enabling metered overage billing.

## 9. Lead Developer orders

### TASK BILL-REL-001 — Durable Stripe subscription event

**Objective**  
Never acknowledge an actionable matched Stripe subscription event until InvoiceIQ owns durable retryable work.

**Reference lesson**  
Provider delivery acknowledgment and business processing are different transactions.

**Current behavior**
`billing.webhook` verifies signature, calls `apply_subscription_event`, catches every exception,
rolls back, then answers 200.

**Target**
1. verify signature;
2. reduce to provider-independent SubscriptionEvent;
3. if irrelevant/no customer → 200 ignored;
4. resolve Stripe customer → org;
5. if unknown customer → 200 ignored + warning;
6. enqueue `billing.apply_subscription_event` with:
   - `org_id=resolved org`;
   - payload = exact reduced event fields;
   - idempotency key = Stripe `event_id`;
7. commit enqueue;
8. only now answer 200;
9. worker reconstructs event, revalidates org/customer match, calls existing idempotent service;
10. handler failure uses normal retry/backoff/DLQ.

**Files**
- `backend/app/api/routes/billing.py`
- `backend/app/services/billing.py`
- `backend/app/services/job_handlers.py`
- `backend/tests/test_billing_stripe.py`

**Do not change**
- signature verification;
- ProcessedStripeEvent business idempotency ledger;
- provider→plan/status mapping;
- Stripe customer IDs;
- tenant guard/RLS;
- EveryPay paths.

**Acceptance**
- duplicate delivery creates one semantic job;
- DB/enqueue failure returns non-2xx;
- business handler failure is retried;
- successful handler applies once;
- unknown/unrelated verified event is harmless 200;
- invalid signature remains 400.

### TASK BILL-METER-001 — Immutable usage-report segment

**Objective**  
A retry must resend the exact same quantity and provider identifier even when internal usage grows.

**Reference lesson**  
Usage events have immutable semantic identity.

**Current behavior**
`delta=count-reported`, identifier ends in current `count`, watermark advances after provider success.

**Target state**
`UsageCounter` adds:
`reporting_target` — cumulative count currently being sent/awaiting acknowledgement.

Algorithm:
1. lock counter;
2. if `reporting_target < reported`, repair to reported (defensive);
3. if `reporting_target == reported` and `count > reported`:
   set `reporting_target=count`;
   COMMIT before provider call;
4. send quantity=`reporting_target-reported`;
5. identifier uses the frozen target;
6. on provider failure: leave target ahead of reported;
7. retry sends same quantity/id;
8. on acknowledged success: set `reported=reporting_target`, commit;
9. next run can create a new segment for later growth.

**Migration**
- add non-null `reporting_target` with 0 default;
- backfill `reporting_target = reported`;
- no new table/RLS policy.

**Acceptance scenario**
- count=10;
- provider records ID/qty then simulates lost response;
- local reported=0,target=10;
- count grows to 15;
- retry sends **10 with the same ID**, not 15/new ID;
- success advances reported=10;
- following run sends **5** with a new target=15 ID.

### TASK BILL-QA-001 — Provider failure scenarios

Create a named billing reliability scenario group covering:
- webhook accepted then worker transient failure;
- webhook redelivery;
- provider accepted meter event then response lost;
- internal count growth during retry;
- provider success then local commit failure where safely simulatable.

The tests assert effects, identifiers, quantities and durable state—not implementation call count alone.

## 10. Areas where InvoiceIQ is already better

InvoiceIQ currently has stronger product-specific controls in:

- Postgres FORCE RLS + ORM tenant guard + explicit org scoping;
- composite tenant-safe foreign keys;
- DB CHECKs on money-adjacent states;
- AR invoice lifecycle tailored to statutory documents;
- immutable issued records outside draft;
- signed append-only AR payment ledger;
- payment-run maker/checker;
- payment/export idempotency;
- explicit safe correction entries;
- Postgres concurrency tests;
- performance-growth CI;
- PII/history quarantine;
- security headers/GDPR erasure;
- monorepo atomic backend/frontend/API changes.

Lago is stronger as evidence for **billing-engine reliability patterns**, not as a replacement architecture.

## 11. Cross-repository synthesis

### Durable external effects

- Scrapling: honor downstream retry timing.
- Paperless: bind validated network destination at connection time.
- Twenty: explicit external-action safety contract.
- Personal Security Checklist: build/repository supply chain is a security boundary.
- Lago: acknowledge inbound provider delivery only after durable ownership; semantic financial operations have immutable identities.
- InvoiceIQ synthesis:
  **validate → persist semantic operation → perform side effect → reconcile/acknowledge → retry same identity**.

### Queue architecture

- Scrapling: bounded task creation/backpressure.
- Lago: isolate queue classes only when resource profiles require it.
- InvoiceIQ: existing Postgres job queue + lane filters is the right current architecture.
  Split worker processes, not queue technology, when measurement says so.

### Financial state

- Lago: keep lifecycle/payment/tax separate.
- InvoiceIQ: already uses stored lifecycle + derived settlement/delivery and signed correction ledgers.
- Decision: current InvoiceIQ model wins for its domain.

## 12. Analysis status

Reference architecture understanding: **96%**  
Important patterns identified: **25**  
High/medium transferable: **17**  
Explicitly rejected/context-specific: **15**  
New P0: **0**  
New P1: **2**  
New P2: **2**  
Selected implementation: **2 patches + regression scenarios**  
Repository delivery: **BLOCKED — GitHub integration returned 403 on branch create**  
Full repository tests: **not run; repository could not be cloned due DNS and branch write is blocked**

---

# Repository 6 — duckdb/duckdb

**Repository:** `duckdb/duckdb`  
**URL:** https://github.com/duckdb/duckdb  
**Reference branch:** `v2.0-cyanoptera`  
**Reference commit:** `e3946f2327a3cc622e1ec7fe71d51de49f93e61d`  
**License:** MIT  
**Analysis date:** 2026-09-07  
**InvoiceIQ source-of-truth:** `kristapsgoncoronoks-ship-it/Bid_it@525470a154d9f2b85941daca783f0a3499ec6526`

## Executive conclusion

DuckDB is a database engine, not a SaaS architecture template. Its directly
transferable value to InvoiceIQ is therefore concentrated in principles:

- push filters/projections/limits to the data engine;
- process bounded batches instead of broad hydrated object graphs;
- make semantic invariants explicit in types/APIs;
- keep logical planning separate from physical execution;
- make performance profiling explain *where* work occurs;
- benchmark execution shape as well as correctness;
- run important correctness suites under alternate configurations;
- preserve fuzz-found bugs as permanent regression cases;
- keep optional capabilities behind explicit, verified boundaries;
- teach coding agents exact repository invariants and deprecated patterns.

One existing InvoiceIQ problem matches these lessons with unusually strong
evidence: `approval_policy.waiting_for()`, called by the measured contended home
dashboard, currently hydrates every pending ApprovalStep + full Invoice objects,
performs a second Vendor selectinload, deduplicates/filter/SoD logic in Python,
and applies the limit only at the end.

The approved adaptation is not DuckDB. It is a single PostgreSQL-oriented,
projected query that performs current-step selection, SoD, assignee filtering,
projection and LIMIT in SQL.

## 1. Repository purpose

DuckDB is an embedded/in-process analytical SQL database. Its main execution path is:

```text
SQL text
  ↓
parser / AST
  ↓
binder + logical plan
  ↓
optimizer
  ↓
physical-plan generation
  ↓
parallel vectorized pipelines
  ↓
storage / scans / joins / aggregates
  ↓
DataChunk result batches
```

It is deliberately optimized for analytical scans/aggregations rather than the
OLTP/server role PostgreSQL fills in InvoiceIQ.

## 2. Architecture

- `src/parser` — SQL grammar/AST.
- `src/planner` — binding/type resolution/logical plan.
- `src/optimizer` — predicate/join/projection rewrites and cost/statistics work.
- `src/execution` — physical operators and vectorized expression execution.
- `src/parallel` — pipelines, task scheduler, dependency events.
- `src/storage` — buffer/compression/checkpoint/table storage/WAL.
- `src/catalog` — single metadata catalog.
- `src/transaction` — MVCC/undo/commit/WAL coordination.
- `src/function` — scalar/aggregate/table/window/pragma functions.
- `extension` — bounded optional capabilities.
- `test` — sqllogictests, API tests, fuzz regressions and OSS-Fuzz harnesses.
- `benchmark` — micro/TPCH/TPCDS and targeted optimizer/operator benchmarks.

The separation exists because the same SQL semantics can have many physical
execution strategies. Parser/planner correctness should not depend on storage
layout; optimizer changes should preserve logical semantics; execution and
storage can evolve behind stable plan/operator contracts.

## 3. Execution and concurrency

DuckDB compiles a physical operator tree into execution pipelines. Pipeline
scheduling is explicit: initialize, execute, prepare-finish, finish and complete
events carry dependency edges. The executor verifies that the schedule is
acyclic, initializes the profiler, readies pipelines and schedules dependency-free
events.

Data flows through columnar vectors/DataChunks rather than Python/row-at-a-time
style objects. The usual vector batch is 2048 rows.

The SaaS lesson is not to imitate vector internals. It is to reduce abstraction
crossings and materialization in analytical read paths.

## 4. Engineering ideas

### DUCK-001 — Push predicates to the data source
**Category:** Performance / Architecture  
**Evidence:** `src/optimizer/filter_pushdown.cpp`.  
**Problem solved:** carrying rows upward only to discard them later.  
**Weakness:** pushdown must preserve semantics across joins/windows/security boundaries.  
**Transferability:** VERY HIGH.  
**InvoiceIQ:** rewrite dashboard AP inbox selection so current-step, assignee and SoD are SQL predicates.

### DUCK-002 — Projection pushdown / remove unused columns
**Category:** Performance  
**Evidence:** `src/optimizer/remove_unused_columns.cpp`.  
**Problem:** decoding/materializing columns no consumer needs.  
**Transferability:** VERY HIGH.  
**InvoiceIQ:** select five inbox fields, not full Invoice/Vendor ORM objects.

### DUCK-003 — Limit before materialization
**Category:** Performance  
**Problem:** computing/hydrating an unbounded candidate set when the UI needs ten rows.  
**Transferability:** VERY HIGH.  
**InvoiceIQ:** SQL `LIMIT 10` after semantic filters/current-step selection.

### DUCK-004 — Vectorized/batched processing
**Category:** Performance  
**Evidence:** DataChunk/vector engine, 2048-row batches.  
**Transferability:** HIGH principle, LOW implementation.  
**InvoiceIQ:** exports/imports/analytics should process bounded batches, not row-by-row ORM loops.

### DUCK-005 — Logical plan separated from physical execution
**Category:** Architecture  
**Problem:** business/logical semantics become coupled to one implementation.  
**Transferability:** MEDIUM.  
**InvoiceIQ:** preserve canonical domain read semantics while optimizing its SQL execution shape.

### DUCK-006 — Semantic invariants encoded in types/APIs
**Category:** Correctness  
**Evidence:** current head introduces `VisibilityBound` so commit/snapshot boundaries cannot be confused as primitive transaction IDs.  
**Transferability:** HIGH selectively.  
**InvoiceIQ:** use stronger wrappers/closed APIs where two primitive IDs/amount semantics have produced real ambiguity; do not perform a broad type refactor without evidence.

### DUCK-007 — Explicit pipeline DAG + cycle verification
**Category:** Orchestration  
**Evidence:** `src/parallel/executor.cpp`.  
**Transferability:** LOW now.  
**InvoiceIQ:** useful only if future workflow/automation becomes a true DAG. Current bounded automation and durable job kinds are simpler.

### DUCK-008 — Profiling shows operator attribution
**Category:** Observability / Performance  
**Evidence:** QueryProfiler tracks query/operator timings, bytes, memory and renders a tree.  
**Transferability:** HIGH principle.  
**InvoiceIQ:** perf artifacts should increasingly record SQL/query-count/EXPLAIN evidence for diagnosed hotspots, not only HTTP duration.

### DUCK-009 — Benchmarks are first-class repository artifacts
**Category:** Testing / Performance  
**Evidence:** benchmark runner, warmup + timed runs, micro + TPCH suites.  
**Transferability:** HIGH.  
**InvoiceIQ:** already strong: scale-growth and concurrency harness. Keep and extend only for concrete findings.

### DUCK-010 — Performance optimization itself has benchmarks
**Category:** Testing  
**Evidence:** dedicated filter-pushdown/join-pushdown benchmarks.  
**Transferability:** HIGH.  
**InvoiceIQ:** PERF-018 fix must be remeasured using the exact dashboard concurrency scenario.

### DUCK-011 — Same correctness suite under alternate configurations
**Category:** QA  
**Evidence:** query-verification, execution, persistence and storage-engine config groups; optimizer-disabled configuration.  
**Transferability:** MEDIUM/HIGH selectively.  
**InvoiceIQ:** retain real-Postgres vs SQLite coverage; add alternate-mode tests only for subsystems with meaningful alternate paths.

### DUCK-012 — Fuzz parsers at the actual vulnerable boundary
**Category:** Security / QA  
**Evidence:** dedicated SQL, CSV/JSON and Parquet fuzz harnesses.  
**Transferability:** MEDIUM.  
**InvoiceIQ:** future targeted fuzz/property tests for CSV/JSON/document/provider payload parsers, not an all-repo fuzzing platform.

### DUCK-013 — Fuzzer findings become normal regression tests
**Category:** QA  
**Evidence:** `test/fuzzer`, AFL CSV regression corpus, OSS-Fuzz issue regressions.  
**Transferability:** HIGH.  
**InvoiceIQ:** every fuzz-discovered parser/import bug becomes a small permanent deterministic test fixture.

### DUCK-014 — Extension boundaries are explicit
**Category:** Architecture  
**Transferability:** LOW now.  
**InvoiceIQ:** no arbitrary server plugin system is required.

### DUCK-015 — Executable extensions are signature-verifiable
**Category:** Security  
**Evidence:** `scripts/verify-extension-signing.sh`.  
**Transferability:** FUTURE.  
**InvoiceIQ:** if executable third-party plugins ever exist, signed/allowlisted provenance is mandatory.

### DUCK-016 — Generated code/files are checked for drift
**Category:** DevOps  
**Evidence:** CI runs generation then `git diff --exit-code`.  
**Transferability:** HIGH principle.  
**InvoiceIQ:** already snapshots/generated-contract tests; keep drift gates.

### DUCK-017 — Debug/assert/sanitizer variants
**Category:** QA  
**Transferability:** LOW for Python-specific implementation, MEDIUM principle.  
**InvoiceIQ:** existing type/lint/real-DB/concurrency gates are more relevant than copying C++ sanitizers.

### DUCK-018 — Query time limit has explicit interrupt boundaries
**Category:** Reliability  
**Transferability:** MEDIUM.  
**InvoiceIQ:** external/request/worker timeouts already more appropriate; don't introduce DB query cancellation globally without incident evidence.

### DUCK-019 — Ownership APIs make invalid memory use harder
**Category:** Code quality  
**Transferability:** LOW implementation.  
**InvoiceIQ:** Python memory ownership is different; learn the invariant-encoding principle only.

### DUCK-020 — Agent instructions include deprecated patterns
**Category:** Engineering governance  
**Evidence:** `AGENTS.md` states exact commands, architecture and old vector loops agents must not add.  
**Transferability:** HIGH.  
**InvoiceIQ:** `engineering-rules.md` already serves this purpose; future agent contract should link to it, not fork it.

### DUCK-021 — Secure boundaries stop optimizer cleverness
**Category:** Security / Correctness  
**Evidence:** filter pushdown explicitly refuses pushing through secure views.  
**Transferability:** HIGH principle.  
**InvoiceIQ:** performance rewrites must never bypass tenant/RLS/permission boundaries. The proposed query keeps explicit `org_id` predicates and existing ORM/RLS backstops.

### DUCK-022 — Single catalog metadata truth
**Category:** Architecture  
**Transferability:** MEDIUM.  
**InvoiceIQ:** canonical service/read-model rules already prevent duplicated calculations; keep ADR-0023.

## 5. What NOT to copy

1. Do not replace PostgreSQL with DuckDB.
2. Do not use DuckDB for OLTP financial writes.
3. Do not build a SQL parser/planner/optimizer.
4. Do not reimplement vectorized physical operators.
5. Do not move InvoiceIQ to C++.
6. Do not copy raw ownership-pointer conventions into Python.
7. Do not create a plugin/extension ecosystem without a product need.
8. Do not introduce DuckDB as a second database merely because analytical reads exist.
9. Do not cache the dashboard before reducing unnecessary work and remeasuring.
10. Do not parallelize one dashboard request with concurrent calls on the same AsyncSession.
11. Do not create several DB sessions merely to mimic execution pipelines; that weakens same-transaction consistency and can increase DB pressure.
12. Do not add dozens of alternate test configurations with no corresponding runtime modes.
13. Do not copy DuckDB's CI supply-chain practice wholesale: its current workflow uses mutable `@main`/major-tag actions, while prior reference learning produced stronger immutable-SHA guidance.
14. Do not turn primitive values into wrapper classes unless they protect an evidenced semantic ambiguity.
15. Do not add indexes for the proposed inbox query until Postgres EXPLAIN/measurement proves one is needed.

## 6. Comparison

### CMP-DUCK-001 — Dashboard AP inbox
**DuckDB principle:** predicate + projection + limit pushdown.  
**InvoiceIQ current:** full ApprovalStep + Invoice hydration; Vendor selectinload; Python current-step/SoD/assignee reduction; Python limit.  
**Problem:** measured dashboard is CPU-bound and repository remediation already names fewer hydrated objects.  
**Decision:** **ADAPT NOW, P2**.  
**Confidence:** HIGH.

### CMP-DUCK-002 — Dashboard cache
**Reference concept:** analytical engines reduce work before materialization.  
**InvoiceIQ option:** cache composed dashboard.  
**Problem:** caching adds freshness, invalidation and permission/module variants.  
**Decision:** **DEFER** until query pushdown is measured.  
**Confidence:** HIGH.

### CMP-DUCK-003 — Performance profiling
**DuckDB:** operator tree + phase timings + bytes/memory.  
**InvoiceIQ:** HTTP scale-shape + concurrency ratios.  
**Decision:** **HYBRID**. Preserve current harness; add query-shape/EXPLAIN artifacts only around diagnosed SQL hotspots.  
**Priority:** P3.

### CMP-DUCK-004 — Alternate configuration testing
**DuckDB:** broad execution/storage/query-verification matrix.  
**InvoiceIQ:** SQLite unit path + real PostgreSQL RLS/migration/concurrency path + optional integrations.  
**Decision:** **KEEP + selective expansion**. No broad matrix.

### CMP-DUCK-005 — Fuzzing
**DuckDB:** dedicated parser/file fuzzers + retained regression corpus.  
**InvoiceIQ:** bounded file parsing/scanning and deterministic tests, no general fuzz campaign.  
**Decision:** **P3 targeted fuzz/property work** for external parser boundaries.

### CMP-DUCK-006 — Embedded analytical database
**DuckDB:** the product is the analytical database.  
**InvoiceIQ:** Postgres is authoritative OLTP/read DB.  
**Decision:** **REJECT NOW / P4 conditional**. Consider DuckDB only for isolated offline export/analytics if Postgres becomes measurably inadequate.

### CMP-DUCK-007 — Semantic type wrappers
**DuckDB:** current MVCC refactor prevents bound/timestamp misuse by type.  
**InvoiceIQ:** many financial states are already closed enums/DB CHECKs, money uses Decimal, composite tenant FKs constrain identity.  
**Decision:** **KEEP current controls; targeted wrappers only after evidence**.

## 7. Engineering debates

### DEBATE DUCK-D1 — Set-based `waiting_for`
**Support:** measured dashboard CPU contention; current code hydrates/reduces broadly; exact DuckDB pushdown lesson.  
**Opposition:** query is more sophisticated (correlated NOT EXISTS, aliases).  
**Alternative A:** keep code.  
**Alternative B:** cache dashboard.  
**Alternative C:** projected SQL query.  
**Cost:** LOW/MEDIUM.  
**Risk:** LOW with existing SoD/current-step/tenant tests.  
**Benefit:** MEDIUM/HIGH on the known hotspot.  
**Lead decision:** **C — ADAPT NOW**.

### DEBATE DUCK-D2 — Cache dashboard
**Support:** avoids repeated aggregate work.  
**Opposition:** freshness/invalidation/permission/module key explosion; masks inefficient source queries.  
**Decision:** **DEFER**. Push down first, benchmark, then reconsider.

### DEBATE DUCK-D3 — Parallelize dashboard calls
**Support:** components are independent reads.  
**Opposition:** AsyncSession is not a concurrent-query abstraction; separate sessions would lose single-transaction snapshot and increase resource use.  
**Decision:** **REJECT NOW**.

### DEBATE DUCK-D4 — Add DuckDB to production
**Support:** excellent OLAP engine.  
**Opposition:** second DB/source-of-truth complexity with no measured PostgreSQL analytical ceiling.  
**Decision:** **REJECT NOW**.

### DEBATE DUCK-D5 — Query-count/shape test
**Support:** PERF-018 is explicitly an execution-shape problem; behavior-only tests did not stop object-hydration regression.  
**Opposition:** implementation-specific tests can be brittle.  
**Decision:** **ADOPT narrowly** on `waiting_for`: exactly one projected SELECT is an intentional performance contract.

### DEBATE DUCK-D6 — Add index for current-step lookup
**Support:** correlated NOT EXISTS can benefit from `(org_id, invoice_id, status, seq)`.  
**Opposition:** existing `(org_id, invoice_id)` + unique `(org_id, invoice_id, seq)` may be sufficient; premature index costs writes/storage.  
**Decision:** **DEFER until EXPLAIN/benchmark**.

## 8. Priority backlog

| ID | Priority | Problem | Adaptation | Complexity | Risk | Validation | Status |
|---|---:|---|---|---|---|---|---|
| PERF-DUCK-001 | P2 | Dashboard AP inbox hydrates/reduces too much in Python | one projected SQL query with current-step/SoD/assignee/limit pushdown | M | L | existing dashboard behavior + one-SELECT regression + perf harness | PATCH READY |
| PERF-DUCK-002 | P2 | PERF-018 needs post-change measurement | rerun scale + concurrency dashboard scenario | S | L | compare to datapoints 5–6 | BLOCKED on repo execution |
| PERF-DUCK-003 | P3 | endpoint timings don't attribute SQL work | optional query count/EXPLAIN artifact for diagnosed reads | M | L | artifact output | DEFER |
| QA-DUCK-001 | P3 | external parser paths not fuzz/property exercised systematically | targeted CSV/JSON/document boundary fuzz tests + regression corpus | M | L/M | deterministic replay corpus | FUTURE |
| SEC-DUCK-001 | P4 | future executable plugin provenance | signed/allowlisted plugins if such system is ever built | M | L | signature-negative tests | FUTURE |
| ANALYTICS-DUCK-001 | P4 | possible future OLAP/export ceiling | evaluate embedded DuckDB only after Postgres evidence | L | M/H | production-scale benchmark | REJECT NOW |

No new P0 or P1 is established from this comparison.

## 9. Lead Developer implementation order

### TASK PERF-DUCK-001 — Push dashboard AP inbox into SQL

**Objective**  
Reduce the CPU/object-allocation work of the measured contended dashboard without changing any user-visible or financial/authorization semantics.

**Reference lesson**  
DuckDB pushes filters/projections/limits toward the scan and moves only the data the consumer needs.

**Current implementation**  
`approval_policy.waiting_for()` loads candidate ApprovalStep + full Invoice ORM objects, selectinloads Vendor rows, identifies current steps in Python, applies SoD/assignee rules in Python, constructs objects, then stops at the UI limit.

**Target architecture**  
One SQL statement:
- current pending step = no earlier pending step for same invoice;
- invoice in live approval workflow states;
- same tenant;
- submitter excluded;
- assigned-to-user or open-to-any approver;
- left/outer vendor name;
- five projected columns only;
- newest first;
- SQL LIMIT.

**Files**
- `backend/app/services/approval_policy.py`
- `backend/tests/test_dashboard.py`

**Do not change**
- ApprovalStep persistence/history.
- Workflow transitions.
- SoD rules.
- authorization matrix.
- tenant guard / RLS.
- dashboard response schema.
- canonical ADR-0023 ownership of the inbox.
- any money arithmetic.
- indexes in this task.

**Tests required**
1. existing `test_dashboard_approvals_waiting_on_me_scoping`;
2. existing canonical dashboard parity;
3. cross-tenant dashboard test;
4. new one-projected-SELECT execution-shape regression;
5. full dashboard module;
6. real Postgres/perf harness;
7. full backend/CI after landing.

**Acceptance**
- identical response semantics;
- later approval step never appears early;
- submitter never sees own item;
- open generic step respects `can_approve_any`;
- tenant isolation unchanged;
- service executes one SELECT for non-empty result;
- no full Invoice/Vendor ORM hydration in this path;
- dashboard concurrency result improves or at minimum does not regress;
- no new index unless measurement proves need.

**Rollback**
Revert the query-only change. No schema/data migration is involved.

## 10. Post-implementation review status

- Reference Repository lens: **PASS** — principle transferred, not C++ implementation.
- Our System Architect: **PASS** — same service/API/DB; no new subsystem.
- Adversarial: **PASS** — simpler runtime, one more sophisticated SQL statement; no cache/index/DB added.
- Security: **PASS WITH REQUIRED REGRESSION** — explicit org filters remain; existing RLS/tenant tests must run.
- QA: **PARTIAL** — proposed patch syntax/query logic can be isolated-validated; full repository tests blocked until code can be applied.
- Lead Developer: **APPROVE FOR BRANCH; NOT DONE**.

## 11. Final answer

After DuckDB, the measurable improvement to implement is:

> Stop using Python/ORM object hydration as an analytical execution engine for
> the dashboard inbox. Express the current-step, tenant, SoD, assignee,
> projection and limit semantics in PostgreSQL, return only the five required
> columns, then remeasure the exact PERF-018 concurrency scenario.

This makes InvoiceIQ better without adding DuckDB, another database, a cache,
an optimizer, a plugin framework, or new infrastructure.

---

# Repository 7 — Stirling-Tools/Stirling-PDF

**Repository:** `Stirling-Tools/Stirling-PDF`  
**Reference URL:** https://github.com/Stirling-Tools/Stirling-PDF  
**Reference branch:** `main`  
**Reference commit:** `02b7b17f9fe2a1abaf60c76a7847ad18e98d1261`  
**Analysis date:** 2026-09-07  
**InvoiceIQ source-of-truth:** `kristapsgoncoronoks-ship-it/Bid_it@525470a154d9f2b85941daca783f0a3499ec6526`

## Executive conclusion

Stirling-PDF is materially relevant to InvoiceIQ because both systems accept
untrusted documents and execute expensive document-reading work. The best
lessons are not Spring/Java/PDFBox. They are operational contracts around
document processing:

1. temporary artifacts have explicit ownership/lifecycle;
2. native processes have per-tool concurrency + timeout budgets;
3. timeout kills the actual child-process tree;
4. large documents use adaptive memory/disk strategies;
5. batch parallelism is bounded and cleans up partial success;
6. pipeline operations are validated and capability-scoped before execution;
7. file/archive expansion is centralized and bounded;
8. CI exercises product variants and pins actions immutably.

InvoiceIQ is already stronger than a generic document processor in several
finance-specific areas: centralized hostile-file rejection, content-addressed
storage, deterministic-first extraction, durable jobs, review gating, typed
capture failures, true OCR progress, tenant isolation and financial workflow
invariants.

The concrete gaps discovered are narrower and more important:

- **STIR-P1-01:** a job lease is fixed at 300 seconds and never renewed.
  `extract_upload()` and `extract_inbound()` leave their domain rows eligible
  while OCR runs. With multiple workers, a legitimate >300 s capture can be
  reclaimed and run concurrently a second time.
- **STIR-P2-01:** both Tesseract invocation paths have no native-process timeout.

Approved design:
- generic lease heartbeat from a separate DB session, guarded by
  `(job id, status=running, locked_by=worker)`;
- per-Tesseract invocation timeout using the already-pinned pytesseract API;
- typed `processing_timeout` capture outcome.

No new queue, Redis, document database, process framework or workflow engine.

## 1. Repository purpose

Stirling-PDF is a self-hostable PDF/document manipulation platform with browser,
desktop and REST interfaces. It supports editing, conversion, OCR, signing,
redaction, compression and pipeline automation. It is designed to operate
privately on the user's infrastructure and integrates both Java PDF processing
and external native tools.

## 2. Architecture map

```text
React SPA / Tauri desktop / REST consumers
                     |
                     v
              Spring API layer
                     |
          validation + tool metadata
                     |
          common/core service layer
              /              \
             /                \
      PDFBox / Java       native tools
      processing          Tesseract/qpdf/
                          LibreOffice/etc.
             \                /
              \              /
               managed temp/data
                     |
            response or next
              pipeline step
```

Build boundaries:

```text
app/common
   |
app/core
   |
app/proprietary (optional distribution feature set)
   |
app/saas (optional; depends on proprietary)
```

The architecture exists because Stirling must support many independent
document transforms, optional native dependencies, multiple distribution
flavors and both one-shot and multi-step workflows.

## 3. Execution flow

### Normal file operation

```text
untrusted upload
  -> controller/API validation
  -> document loader/temp materialization
  -> Java PDF operation OR bounded native process
  -> managed intermediate/output
  -> response
  -> cleanup
```

### Pipeline

```text
PipelineConfig
  -> create run context
  -> validate operation exists
  -> validate parameters + input capabilities
  -> execute operation
  -> normalize/expand output when needed
  -> feed next step
  -> collect result/errors
  -> clean temporary artifacts
```

A key implementation choice is that Stirling's PipelineProcessor dispatches some
steps back through an internal HTTP API. The principle (reuse the same operation
contract) is useful; the mechanism is not recommended for InvoiceIQ.

## 4. Top engineering ideas

### STIR-001 — Managed temporary-file ownership
**Category:** Reliability / Security  
**Evidence:** `TempFileManager`, `TempFile`, `TempFileRegistry`.  
**Problem:** temp artifacts leak when every controller invents cleanup.  
**Why it works:** creation and cleanup use one lifecycle and `AutoCloseable`.  
**Weakness:** centralized temp registries add machinery where code is mostly in-memory.  
**Transferability:** MEDIUM now / HIGH if InvoiceIQ adds native document conversion.

### STIR-002 — Startup + scheduled stale-temp cleanup
**Category:** Operations  
**Evidence:** `TempFileCleanupService`.  
**Problem:** process/container crashes bypass normal `finally` cleanup.  
**Why it works:** normal ownership cleanup plus age-based recovery.  
**Weakness:** name-pattern cleanup is operationally delicate.  
**Transferability:** LOW now because InvoiceIQ primarily uses content-addressed object storage.

### STIR-003 — Per-native-tool concurrency gates
**Category:** Performance / Reliability  
**Evidence:** `ProcessExecutor`.  
**Problem:** many simultaneous OCR/conversion processes exhaust CPU/RAM.  
**Why it works:** semaphore is tied to the actual expensive resource.  
**Transferability:** MEDIUM. InvoiceIQ already has worker lanes; one worker is serial.
Use separate process limits only if operators run multiple OCR workers and measurements show pressure.

### STIR-004 — Per-native-tool execution timeout
**Category:** Reliability / Security  
**Evidence:** `ProcessExecutor`, `ApplicationProperties.processExecutor.timeoutMinutes`.  
**Problem:** hostile/pathological input or tool bugs can run forever.  
**Transferability:** **VERY HIGH**. InvoiceIQ's pytesseract calls are currently unbounded.

### STIR-005 — Kill descendant process tree on timeout
**Category:** Reliability  
**Problem:** killing only the wrapper can leave child processes consuming resources.  
**Transferability:** HIGH principle. pytesseract already terminates its Tesseract child
when its timeout fires, so InvoiceIQ can use the library's existing mechanism.

### STIR-006 — Drain stdout/stderr concurrently
**Category:** Reliability  
**Problem:** child process deadlocks when one pipe fills.  
**Transferability:** LOW now because InvoiceIQ does not own raw Popen plumbing for OCR.

### STIR-007 — Validate command arguments before native execution
**Category:** Security  
**Problem:** malformed command/path input reaches native process boundary.  
**Transferability:** FUTURE. InvoiceIQ does not expose user-controlled native commands.

### STIR-008 — Adaptive memory vs file-backed document loading
**Category:** Performance  
**Evidence:** `CustomPDFDocumentFactory`.  
**Problem:** large PDFs can double memory peaks or exhaust heap.  
**Transferability:** MEDIUM/LATER. InvoiceIQ currently caps general uploads at 15 MB.

### STIR-009 — Avoid double-peak serialization memory
**Category:** Performance  
**Evidence:** large PDF save goes to temp before reading final bytes.  
**Transferability:** MEDIUM if InvoiceIQ creates large generated PDFs/archives at scale.

### STIR-010 — Bounded batch parallelism with fail-cleanup
**Category:** Performance / Reliability  
**Evidence:** `CustomPDFDocumentFactory.loadAll/saveAll...`.  
**Problem:** unbounded parallel file work causes memory pressure; partial failure leaks resources.  
**Transferability:** HIGH principle. Existing InvoiceIQ worker lanes are the preferred implementation.

### STIR-011 — Central hardened archive extraction
**Category:** Security  
**Evidence:** `ZipExtractionUtils`, max nested ZIP depth.  
**Transferability:** LOW now. InvoiceIQ intentionally rejects archives at file-security ingress.

### STIR-012 — Validate pipeline operation before execution
**Category:** Workflow / Security  
**Evidence:** `PipelineProcessor` + API docs/tool metadata.  
**Transferability:** HIGH principle. InvoiceIQ's bounded automation action catalog already follows it.

### STIR-013 — Capability metadata for automation eligibility
**Category:** Architecture  
**Problem:** not every UI operation is safe/meaningful in automation.  
**Transferability:** HIGH future principle, already aligned with InvoiceIQ's explicit action allowlists.

### STIR-014 — Run-scoped automation correlation context
**Category:** Observability  
**Problem:** a multi-step workflow needs one traceable identity.  
**Transferability:** HIGH future principle; aligns with prior Twenty agent/run identity learning.

### STIR-015 — Optional dependency detection / graceful degradation
**Category:** Product / Operations  
**Problem:** self-hosted installs vary in native capabilities.  
**Transferability:** MEDIUM. InvoiceIQ already lazily imports OCR/ClamAV/provider features.

### STIR-016 — Build-time product-flavor boundaries
**Category:** Architecture / DevOps  
**Evidence:** `settings.gradle` core/proprietary/saas.  
**Transferability:** LOW now. InvoiceIQ is not maintaining three distribution products.

### STIR-017 — Full-SHA GitHub Action pinning
**Category:** Supply chain  
**Evidence:** `.github/workflows/backend-build.yml`.  
**Transferability:** HIGH; cross-repository confirmation of the Paperless-derived action-pinning work.

### STIR-018 — Test multiple product flavors
**Category:** QA  
**Problem:** optional build combinations silently rot.  
**Transferability:** LOW unless InvoiceIQ introduces real build flavors.

### STIR-019 — Test reports are themselves asserted to exist
**Category:** CI correctness  
**Problem:** a green "test" step can be misconfigured and execute nothing.  
**Transferability:** MEDIUM/HIGH for important generated/gated artifacts.

### STIR-020 — Resource policy is configuration, not scattered literals
**Category:** Operations  
**Evidence:** session limits/timeouts live under process executor configuration.  
**Transferability:** HIGH. The approved OCR timeout belongs in `Settings`, not inside one provider.

### STIR-021 — Input-size strategy and live memory snapshot are separate inputs
**Category:** Performance  
**Evidence:** `CustomPDFDocumentFactory.MemorySnapshot`.  
**Transferability:** LOW now; useful if upload/document limits grow.

### STIR-022 — Core code and proprietary code have license/distribution boundaries
**Category:** Governance  
**Evidence:** root LICENSE delegates proprietary/SaaS/engine/frontend areas to their own licenses.  
**Transferability:** LOW currently but important if InvoiceIQ ever becomes open-core.

## 5. Patterns we should NOT adopt

1. **Spring/Java rewrite.** No problem solved.
2. **Generic PDF tool platform.** InvoiceIQ is a financial workflow product.
3. **Internal loopback HTTP for service orchestration.** Reuse service contracts directly.
4. **Generic no-code document pipeline graph.** Current bounded automations are safer and simpler.
5. **Archive ingestion today.** InvoiceIQ deliberately rejects ZIP/RAR/7z at the security gate.
6. **Client IndexedDB as authoritative finance document storage.**
7. **Optional authentication mode.** InvoiceIQ's tenant finance data requires auth.
8. **LibreOffice/qpdf/Ghostscript/ImageMagick stack without a product requirement.**
9. **Open-core build-flavor system now.**
10. **Global static process-executor registry pattern.** Use explicit services/config if such a layer is ever needed.
11. **Aggressive system-temp pattern cleanup today.** InvoiceIQ does not own enough native temp artifacts to justify it.
12. **Adaptive spill-to-disk before memory profiling shows a need.**
13. **Parallel AsyncSession work to mimic Stirling's virtual-thread batches.**
14. **A new semaphore inside each InvoiceIQ OCR call while each worker already executes one job at a time.**
15. **Copy proprietary/SaaS source.** We are transferring principles, not restricted code.

## 6. Where InvoiceIQ is already better for its product

### Hostile-file ingress
InvoiceIQ centrally validates size, magic/type parity, rejects executables,
archives and script content, supports fail-closed ClamAV when configured, and
uses defused XML parsing. This is exactly appropriate for invoices.

### Durable job semantics
InvoiceIQ uses a Postgres-backed durable queue with atomic claiming,
idempotency, retries, dead-letter and lanes rather than process-local document
automation state.

### Human review
OCR output is low-confidence and review-gated before becoming a financial
invoice. Stirling's generic transforms do not need this finance-specific invariant.

### Honest progress
InvoiceIQ publishes real parser phases and real OCR page counts only when
measured; it explicitly refuses invented percentage progress.

### Failure operations
Capture failure is a typed worklist outcome with remediation, retry usefulness,
acknowledgement and retained-document semantics.

### Tenant isolation and financial invariants
Shared-schema org guard + RLS + explicit route capabilities + DB constraints are
more appropriate to InvoiceIQ than a self-hosted single-workspace document tool.

## 7. Evidence-backed gaps

### GAP STIR-G1 — live job leases are not renewable
`jobs.STALE_LEASE_SECONDS = 300`. Claim stamps `locked_at`; `reclaim_stale`
requeues a RUNNING job once the timestamp is old. No heartbeat exists.

Both async document handlers can exceed this:
- PDF OCR supports up to 50 pages.
- product docs explicitly describe a 40-page scan taking minutes.
- Tesseract calls were unbounded.

The capture rows remain `queued` while parsing. Therefore after stale reclaim a
second worker still sees the capture as eligible and can start the same parse.

This is not merely wasted CPU:
- two parses can write the same extraction run;
- provenance rows can be written twice;
- a success/failure race can overwrite capture state;
- the queue can report a terminal result while the original owner still runs.

**Priority: P1.**

### GAP STIR-G2 — native Tesseract invocation has no runtime bound
Both:
- PDF `image_to_data(...)`
- image `image_to_string(...)`
omit pytesseract's timeout.

pytesseract 0.3.13 already supports `timeout=` and terminates the Tesseract
process on expiry. No new dependency/process abstraction is needed.

**Priority: P2.**

## 8. Comparisons

### CMP-STIR-001 — external native tool lifetime
**Reference:** central per-tool timeouts.  
**InvoiceIQ:** no Tesseract timeout; page cap only.  
**Recommendation:** **ADAPT NOW** using pytesseract's own timeout.  
**Confidence:** HIGH.

### CMP-STIR-002 — long-running work ownership
**Reference:** native process resource limits; operations remain owned for their lifetime.  
**InvoiceIQ:** durable queue lease expires based only on original claim timestamp.  
**Recommendation:** **ADOPT lease-heartbeat principle** in the existing DB queue.  
**Confidence:** VERY HIGH.

### CMP-STIR-003 — process concurrency
**Reference:** semaphore per native tool.  
**InvoiceIQ:** each worker drains jobs serially; lane count is an operator scaling choice.  
**Recommendation:** **KEEP OUR APPROACH** until measurements show multiple OCR workers overload the host.

### CMP-STIR-004 — temporary workspace
**Reference:** centralized temp registry + AutoCloseable + stale cleanup.  
**InvoiceIQ:** bytes/object storage and in-memory parser path; minimal ad-hoc native temp use.  
**Recommendation:** **DEFER**. Adopt when a second native converter or substantial temp-file path appears.

### CMP-STIR-005 — document materialization
**Reference:** size + heap-adaptive memory/disk.  
**InvoiceIQ:** 15 MB ingress cap and worker-tier parsing.  
**Recommendation:** **NO CHANGE NOW**.

### CMP-STIR-006 — archive extraction
**Reference:** hardened nested ZIP extraction.  
**InvoiceIQ:** reject archives at ingress.  
**Recommendation:** **KEEP OUR APPROACH**; smaller attack surface.

### CMP-STIR-007 — workflow engine
**Reference:** generic document-operation pipeline.  
**InvoiceIQ:** bounded trigger-condition-action financial automation.  
**Recommendation:** **KEEP OUR APPROACH**.

### CMP-STIR-008 — internal service reuse
**Reference:** pipeline can loop back through internal HTTP APIs.  
**InvoiceIQ:** service functions/job handlers share domain logic directly.  
**Recommendation:** **KEEP OUR APPROACH**.

### CMP-STIR-009 — GitHub Action pinning
**Reference:** SHA-pinned actions in backend CI.  
**InvoiceIQ:** prior Paperless cycle already identified/patched this governance pattern.  
**Recommendation:** **cross-repo confirmation; do not create a duplicate project**.

## 9. Engineering debates

### DEBATE STIR-D1 — Add generic queue lease heartbeat
**Proposal:** renew RUNNING job `locked_at` while handler is alive.

**Support:** the queue supports several workers; 300 s reclaim is fixed;
document work can legitimately last minutes; capture rows stay eligible during
parse.

**Opposition:** adds one background coroutine and separate DB transaction for
jobs >60 seconds.

**Alternatives**
A. increase stale lease globally;  
B. mark extraction row running;  
C. heartbeat current job;  
D. disable multiple OCR workers.

**Why A fails:** merely moves the race and slows real crash recovery.  
**Why B fails:** crashed work would remain `running` and reclaimed job would skip it.  
**Why D fails:** does not protect other long jobs and sacrifices scaling.

**Cost:** MEDIUM.  
**Risk:** LOW/MEDIUM.  
**Benefit:** HIGH.  
**Lead:** **ADOPT C — P1.**

### DEBATE STIR-D2 — Add Tesseract timeout
**Proposal:** `Settings.ocr_process_timeout_seconds`, default 120, upper bound 300.

**Support:** native process is currently unbounded; pytesseract already provides
termination; no dependency.

**Opposition:** too-low values can reject unusually hard pages.

**Alternative:** generic ProcessExecutor wrapper.

**Decision:** **ADAPT SIMPLE VERSION — P2.**
Start with pytesseract's API. Do not build generic native-process infrastructure
for one tool.

### DEBATE STIR-D3 — Add per-process OCR semaphore
**Proposal:** semaphore like Stirling.

**Opposition:** one InvoiceIQ worker runs one job at a time and lanes already
provide resource isolation. The effective concurrency is the number of OCR
workers, which deployment controls.

**Decision:** **REJECT NOW.** Measure before adding a second concurrency system.

### DEBATE STIR-D4 — Managed temp workspace
**Decision:** **DEFER P4.**
Trigger: second substantial native converter OR measurable temp leakage/memory
pressure.

### DEBATE STIR-D5 — Generic document workflow graph
**Decision:** **REJECT.**
InvoiceIQ's financial actions need explicit state/permission/human controls, not
a generic transform graph.

## 10. Priority backlog

| ID | Priority | Problem | Adaptation | Benefit | Complexity | Risk | Status |
|---|---:|---|---|---|---|---|---|
| STIR-P1-01 | P1 | live job can become "stale" while still executing | guarded 60 s lease heartbeat using separate DB session | High | Medium | Low/Med | PATCH READY |
| STIR-P2-01 | P2 | Tesseract native process unbounded | configurable 120 s per invocation timeout + typed failure | High | Low | Low | PATCH READY |
| STIR-P3-01 | P3 | native-tool resource usage not directly measured | add OCR duration/timeout counters if operational need emerges | Medium | Low | Low | DEFER |
| STIR-P3-02 | P3 | CI supply-chain pins | prior Paperless action-pin work; Stirling confirms | Medium | Low | Low | EXISTING BACKLOG |
| STIR-P4-01 | P4 | future native temp artifacts | scoped managed temp workspace + stale cleanup | Medium | Medium | Low | FUTURE |
| STIR-P4-02 | P4 | future multi-native-tool resource contention | per-tool concurrency budgets | Medium | Medium | Low | FUTURE |
| STIR-P4-03 | P4 | future large document generation | adaptive memory/spill strategy | Medium | Medium | Medium | FUTURE |

No new P0.

## 11. Lead Developer implementation orders

### TASK STIR-P1-01 — Durable job lease heartbeat

**Objective**  
A worker actively executing a job must never be reclaimed merely because the
handler legitimately runs longer than 300 seconds.

**Reference lesson**  
Resource ownership must last for the operation lifetime. Stirling enforces this
at external-process execution boundaries; InvoiceIQ should enforce it at its
durable queue boundary.

**Current behavior**
- claim writes `RUNNING`, `locked_by`, `locked_at`;
- no update occurs during handler execution;
- another worker can call `reclaim_stale` after 300 seconds;
- upload/email extraction domain rows still look eligible.

**Target**
`run_once` starts a heartbeat coroutine after tenant scope is installed.
Every 60 seconds it opens a *separate* `SessionLocal`, sets the job's org scope,
and executes:

```text
UPDATE jobs
SET locked_at = now
WHERE id = :id
  AND status = 'running'
  AND locked_by = :worker
```

Zero rows means ownership/status changed: stop heartbeating.

Handler completion/error:
1. signal heartbeat stop;
2. await heartbeat task exit;
3. reset tenant context.

**Files**
- `backend/app/services/jobs.py`
- `backend/tests/test_jobs.py`

**Do not change**
- queue storage;
- claim ordering;
- SKIP LOCKED semantics;
- attempts/backoff;
- dead-letter behavior;
- stale lease duration in this task;
- worker lanes;
- job kinds;
- handler API;
- extraction run statuses.

**Tests**
- owner can renew;
- wrong worker cannot renew;
- terminal job cannot be renewed/resurrected;
- running handler actually causes heartbeat;
- live renewed job is not stale-reclaimed;
- existing crashed stale job still reclaims;
- max-attempt stale job still dead-letters;
- Postgres/RLS test with a separate heartbeat session;
- two-worker long-job regression preferred for certification.

**Acceptance**
- no live worker's job is reclaimable while heartbeats succeed;
- heartbeat never shares handler AsyncSession;
- heartbeat can never change a non-RUNNING or other-worker row;
- heartbeat failure logs but does not fail business handler;
- after actual worker death, lease naturally expires and existing recovery works.

**Rollback**
Pure application-code revert. No migration.

### TASK STIR-P2-01 — Bounded Tesseract runtime

**Objective**  
A single hostile/pathological PDF page or image cannot occupy a Tesseract
process indefinitely.

**Reference lesson**  
Every native operation has a runtime budget. Use the smallest existing native
seam rather than introducing a process framework.

**Current**
- PDF OCR has 50-page cap, but each page's Tesseract process is unbounded;
- image OCR's Tesseract process is unbounded.

**Target**
- `Settings.ocr_process_timeout_seconds = 120`, >0 and <=300;
- pass `timeout=` to `image_to_data` and `image_to_string`;
- keep `TesseractError` as ordinary unreadable/parser failure;
- wrap pytesseract timeout RuntimeError as `OcrTimedOut`;
- map it to stable `processing_timeout`;
- failed-capture UI receives actionable retry/remediation semantics.

**Files**
- `backend/app/core/config.py`
- `backend/app/services/pdf_ocr.py`
- `backend/app/services/extraction_provider.py`
- `backend/app/services/capture_failures.py`
- `backend/tests/test_pdf_ocr.py`

**Do not change**
- deterministic-first provider order;
- OCR DPI;
- 50-page cap;
- confidence thresholds;
- human-review gate;
- file-security ingress;
- ClamAV policy;
- worker architecture.

**Tests**
- PDF Tesseract call receives configured timeout;
- simulated timeout becomes `OcrTimedOut`;
- PdfProvider maps timeout to `processing_timeout`;
- image provider passes same timeout and classification;
- ordinary TesseractError remains unreadable/non-timeout;
- real scanned-PDF canary still passes where Tesseract is installed;
- capture failure worklist renders stable timeout remediation.

**Acceptance**
- no Tesseract invocation is unbounded;
- no timeout is mislabeled as malformed invoice/internal error;
- normal OCR output unchanged;
- setting is operator-overridable via environment;
- no new dependency.

**Rollback**
Application-only revert; setting can remain unused without data impact.

## 12. Post-implementation architecture decision

The target InvoiceIQ architecture remains:

```text
untrusted file
  -> centralized file security
  -> content-addressed durable storage
  -> durable job
  -> renewable job lease
  -> worker lane
  -> deterministic parser
  -> bounded native OCR process
  -> typed outcome + real progress
  -> human review
  -> financial record
```

That architecture is more appropriate to InvoiceIQ than copying Stirling's PDF
platform, pipeline framework or native tool stack.

---

# Repository 8 — pallets/flask

**Repository:** `pallets/flask`  
**Reference URL:** https://github.com/pallets/flask  
**Reference branch:** `main`  
**Reference commit:** `d318b683471101618febed18996405ad26462110`  
**Analysis date:** 2026-09-07  
**InvoiceIQ source-of-truth:** `kristapsgoncoronoks-ship-it/Bid_it@525470a154d9f2b85941daca783f0a3499ec6526`

## Executive conclusion

Flask is valuable to InvoiceIQ primarily as a study in *framework contracts*:
setup-versus-runtime boundaries, request context lifetime, modular registration,
extension design, staged backwards compatibility, cryptographic key rotation,
and unusually disciplined compatibility testing.

The right transfer is not Flask's WSGI architecture. InvoiceIQ already has the
better product-specific stack: FastAPI/Pydantic, explicit dependency injection,
framework-agnostic domain errors, durable jobs, server-side revocable sessions,
tenant ContextVars + Postgres RLS, typed OpenAPI, and finance-specific
invariants.

Three useful gaps were found:

1. **FLASK-P2-01 — cryptographic purpose separation + staged JWT signing-key
   rotation.** InvoiceIQ signs access tokens and OIDC state with the same
   `secret_key` that also derives the local key-vault KEK. A literal Flask-style
   `SECRET_KEY_FALLBACKS` copy would couple auth rotation to stored-secret
   encryption. Adapt by introducing a dedicated optional JWT signing key and
   verify-only fallbacks while keeping the application/local-KEK secret stable.
2. **FLASK-P2-02 — route-topology guard.** InvoiceIQ already contains a manual
   router-order warning because `/issued/{invoice_id}` can shadow
   `/issued/recurring`. Convert that tribal knowledge into a structural CI test
   using Starlette's compiled route regex.
3. **FLASK-P3-01 — deprecation-warning ratchet.** Flask treats warnings as test
   failures. InvoiceIQ currently ignores every `DeprecationWarning`. Do not flip
   blindly; first inventory and fix/allowlist exact third-party warnings, then
   make new warnings fail CI.

No new P0/P1 was discovered in this Flask cycle.

## 1. Repository purpose

Flask is a compact WSGI web framework. It supplies application construction,
routing integration, request/application context, response/error lifecycle,
configuration, session interfaces, templates, testing helpers, CLI support and
extension hooks while relying on Werkzeug for the HTTP/routing substrate.

Its enduring strength is that the public surface stays small while the internal
contracts for setup, extension composition and request lifetime are explicit.

## 2. Architecture map

```text
                  SETUP TIME
                        |
             sansio.Scaffold / App
                        |
        routes / hooks / handlers / config
             ^                     ^
             |                     |
         Blueprints             Extensions
         (deferred)             init_app()
                        |
                setup becomes fixed
                        |
                  RUNTIME
                        |
                  WSGI request
                        |
                  AppContext
          app + request + session + g
                        |
                  URL matching
                        |
                preprocess_request
                        |
                 dispatch_request
                        |
        layered exception / HTTP handling
                        |
                 finalize_request
                        |
              after hooks + teardown
                        |
                  context pop
```

A notable design split is `flask.sansio`: registration/configuration behavior can
exist independently of the WSGI request transport. This makes the setup model
easier to reason about and reuse.

## 3. Major execution flow

```text
WSGI environ
  -> AppContext.from_environ()
  -> push context
  -> open session
  -> URL match
  -> request_started
  -> preprocess_request()
  -> dispatch_request()
  -> handle_user_exception()/handle_http_exception()
  -> make/finalize response
  -> after_request
  -> teardown_request / teardown_appcontext
  -> context pop
```

Unhandled failures still go through response finalization. Flask preserves the
original exception on the generated 500 object and emits a request-exception
signal before rendering the response.

## 4. Top engineering ideas

### FLASK-001 — Setup/runtime phase separation
**Category:** Architecture  
**Evidence:** `sansio/scaffold.py::setupmethod`,
`sansio/app.py::_check_setup_finished`.  
**Problem:** mutating globally registered routes/hooks after workers begin
serving causes inconsistent behavior.  
**Why it works:** setup methods refuse to execute after runtime begins.  
**Trade-off:** dynamic runtime plugin installation is deliberately harder.  
**Transferability:** HIGH principle.

### FLASK-002 — Deferred modular registration
**Category:** Architecture  
**Evidence:** `Blueprint.deferred_functions`,
`BlueprintSetupState`, `Blueprint.register`.  
**Problem:** modules need to declare routes/hooks without owning the root app.  
**Transferability:** MEDIUM. FastAPI's APIRouter already gives InvoiceIQ the
appropriate mechanism; do not recreate Blueprint.

### FLASK-003 — Explicit context lifetime
**Category:** Architecture / Reliability  
**Evidence:** `ctx.AppContext`, ContextVar token push/pop, nested push count.  
**Problem:** request-local state must not leak across requests/tasks.  
**Transferability:** HIGH principle, ALREADY ADOPTED in InvoiceIQ tenant/actor
ContextVars plus request-scoped dependencies.

### FLASK-004 — Transport-free setup core
**Category:** Architecture  
**Evidence:** `flask.sansio`.  
**Problem:** configuration/routing registration should not require live request
I/O.  
**Transferability:** HIGH principle, ALREADY present in InvoiceIQ's framework-free
domain/service layer.

### FLASK-005 — Extension instance does not own the app
**Category:** Modularity  
**Evidence:** extension-development guide's `init_app` pattern.  
**Problem:** global app capture makes factories/testing/multiple configs hard.  
**Transferability:** MEDIUM/FUTURE; useful only if InvoiceIQ exposes true plugin
packages.

### FLASK-006 — App-specific extension state has a namespace
**Category:** Modularity  
**Evidence:** `app.extensions`.  
**Transferability:** LOW now; InvoiceIQ does not need a third-party extension
ecosystem.

### FLASK-007 — Error resolution is layered and specific
**Category:** Backend  
**Evidence:** HTTP-code + exception-MRO + blueprint-scoped handler lookup.  
**Transferability:** MEDIUM principle. InvoiceIQ's typed `AppError` with stable
machine codes is a better application-domain contract.

### FLASK-008 — Finalization also happens on errors
**Category:** Reliability  
**Evidence:** `handle_exception()` → `finalize_request(...)`.  
**Transferability:** HIGH principle, already achieved by InvoiceIQ's ASGI
middleware/error boundary.

### FLASK-009 — Staged compatibility bridge
**Category:** API evolution  
**Evidence:** Flask 3.2 detects subclasses overriding old context method
signatures, emits deprecation warnings, wraps compatibility calls, and states the
Flask 4.0 removal boundary.  
**Problem:** library users need migration time.  
**Transferability:** HIGH for public InvoiceIQ API/provider/automation contracts.

### FLASK-010 — Current-sign / old-verify cryptographic rotation
**Category:** Security / Operations  
**Evidence:** `SECRET_KEY_FALLBACKS` in session signing.  
**Problem:** routine key rotation should not force every active session to die at
one instant.  
**Transferability:** VERY HIGH, but **must be adapted with key-purpose separation**.

### FLASK-011 — Null capability object
**Category:** Error handling  
**Evidence:** `NullSession`: reads are harmless, mutations fail with a diagnostic.  
**Transferability:** MEDIUM selectively. Useful only when an optional capability
has a meaningful inert/read-only mode; fail closed for security features.

### FLASK-012 — Central resource-limit configuration
**Category:** Security / Performance  
**Evidence:** content/form memory/part limits, trusted hosts, cookie policies.  
**Transferability:** HIGH principle; InvoiceIQ already has stronger document
ingress caps and security policies.

### FLASK-013 — Warnings are CI failures
**Category:** Testing  
**Evidence:** pytest `filterwarnings = ["error"]`.  
**Problem:** deprecations become removal bugs if ignored for months.  
**Transferability:** HIGH, but use a ratchet because InvoiceIQ currently ignores
all DeprecationWarnings.

### FLASK-014 — Minimum dependency compatibility tests
**Category:** Testing  
**Evidence:** `tests-min` tox env.  
**Transferability:** LOW for InvoiceIQ, which deploys exact-pinned application
dependencies rather than publishing a reusable framework.

### FLASK-015 — Upstream-development dependency tests
**Category:** Testing  
**Evidence:** `tests-dev` installs Pallets dependencies from their main branches.  
**Transferability:** LOW/P4; only justified as a scheduled canary if dependency
breakage becomes a measured maintenance problem.

### FLASK-016 — Wide interpreter/OS compatibility matrix
**Category:** CI  
**Evidence:** CPython 3.10–3.15, free-threaded Python, PyPy, Windows, macOS.  
**Transferability:** LOW for a Linux-container SaaS; excellent for a framework.

### FLASK-017 — Strict static typing as a compatibility gate
**Category:** Code quality  
**Evidence:** strict mypy + pyright type tests.  
**Transferability:** MEDIUM principle. InvoiceIQ intentionally has an incremental
typing ratchet; keep that plan instead of enabling framework-level strictness
overnight.

### FLASK-018 — Framework-aware testing helpers
**Category:** Testing  
**Evidence:** `FlaskClient`, context preservation, session transaction helpers.  
**Transferability:** MEDIUM. InvoiceIQ already has FastAPI/ASGI fixtures and
domain-specific helpers; add helpers only where tests duplicate lifecycle setup.

### FLASK-019 — Zero default workflow permissions
**Category:** DevSecOps  
**Evidence:** CI `permissions: {}`.  
**Transferability:** HIGH; reinforces prior supply-chain findings.

### FLASK-020 — Immutable action pins + no persisted checkout credentials
**Category:** DevSecOps  
**Evidence:** Flask CI uses full SHAs and `persist-credentials:false`.  
**Transferability:** HIGH; cross-repository confirmation of existing InvoiceIQ
action-pinning work.

### FLASK-021 — Cancel obsolete CI work
**Category:** DevOps  
**Evidence:** workflow concurrency with `cancel-in-progress:true`.  
**Transferability:** HIGH where safe; no need for a Flask-derived duplicate task
if InvoiceIQ already has workflow concurrency.

### FLASK-022 — Configuration loaders have deterministic precedence
**Category:** Configuration  
**Evidence:** config mapping/files/prefixed env and sorted nested env processing.  
**Transferability:** LOW implementation. Pydantic Settings is safer and more
typed for InvoiceIQ.

### FLASK-023 — Route/module namespaces prevent naming collisions
**Category:** Architecture  
**Evidence:** nested Blueprint dotted names and duplicate registration checks.  
**Transferability:** MEDIUM. FastAPI router organization plus the new topology
test is the right native adaptation.

### FLASK-024 — Lazy capability acquisition
**Category:** Performance  
**Evidence:** session/context resources initialized when needed in several
interfaces.  
**Transferability:** MEDIUM principle; InvoiceIQ already lazily imports optional
providers and uses request dependencies.

## 5. Patterns not to copy

1. Flask/WSGI rewrite.
2. `current_app`, `request`, `g` proxy layer over FastAPI dependencies.
3. Flask Blueprint implementation on top of APIRouter.
4. Signed-cookie authoritative user sessions for a finance SaaS that already has
   revocable server-side sessions.
5. One shared rotating `secret_key` for JWT and local secret encryption.
6. App-factory rewrite without a multi-app runtime/test requirement.
7. Generic Flask extension ecosystem before InvoiceIQ supports third-party
   plugins.
8. Minimum-version dependency matrix for an exact-pinned deployed application.
9. Windows/macOS/PyPy/free-threaded production CI merely because Flask needs it.
10. In-process signal bus replacing durable jobs/webhooks/audit seams.
11. Blanket strict-mypy/pyright rollout across a mature codebase.
12. Blanket warnings-as-errors flip before knowing the warning inventory.
13. Mutable runtime provider installation as a public product feature.
14. Flask NullSession-style graceful degradation for security-critical
    capabilities: security should fail closed.
15. Error-MRO matching as a replacement for stable domain machine codes.

## 6. Areas where InvoiceIQ is already better for its product

### Typed service errors
InvoiceIQ services raise framework-independent `AppError` subclasses with stable
machine codes. The FastAPI boundary maps them to the wire. This is more useful to
a finance SPA/API than relying on exception-class hierarchy alone.

### Explicit DI
FastAPI dependencies make identity, tenant, permissions and DB requirements
visible in function signatures. Replacing them with request proxies would reduce
auditability.

### Revocable sessions
A JWT signature is only the first gate. The token's `jti` must map to a live
server-side Session, then active user/membership/org checks pass. This permits
immediate logout/password-reset revocation.

### Tenant isolation
ContextVar scope + ORM criteria + Postgres RLS are stronger product-specific
boundaries than Flask provides as a generic framework.

### API contract drift gate
InvoiceIQ regenerates and byte-compares a checked-in OpenAPI contract. Flask's
library compatibility testing is excellent, but InvoiceIQ already has a stronger
machine-visible application API source of truth.

### Application configuration typing
Pydantic Settings validates types and production invariants at startup. Keep it
over Flask's dict/config-file model.

## 7. Evidence-backed InvoiceIQ gaps

### GAP FLASK-G1 — JWT rotation is coupled to application/local-KEK secret

Access tokens and OIDC state use `settings.secret_key`. The local key vault also
derives its AES KEK from `settings.secret_key` and explicitly documents that
rotating the app secret invalidates locally sealed values.

A routine auth signing-key rotation therefore currently presents an avoidable
choice:
- invalidate all outstanding signed tokens, or
- change the shared app secret and risk local-keyvault decryptability.

**Priority: P2.**

### GAP FLASK-G2 — Route reachability depends on a manual ordering rule

`app/api/router.py` explicitly requires the recurring router before the issued
router because `/issued/{invoice_id}` can shadow `/issued/recurring*`.

That means correctness depends on a comment and human memory.

**Priority: P2.**

### GAP FLASK-G3 — All DeprecationWarnings are globally suppressed

`pytest.ini` ignores `DeprecationWarning`. This can hide framework/dependency
migration work until an upgrade removes an API.

**Priority: P3** because the actual current warning set must be inventoried before
enforcement.

## 8. Comparisons

### CMP-FLASK-001 — Modular route composition
**Flask:** Blueprint deferred setup.  
**InvoiceIQ:** FastAPI APIRouter hierarchy.  
**Decision:** KEEP OUR APPROACH.  
**Confidence:** HIGH.

### CMP-FLASK-002 — Setup immutability
**Flask:** mutations rejected after first request / Blueprint registration.  
**InvoiceIQ:** route tree is import-built; parser registries remain mutable.  
**Decision:** ADAPT only where evidence exists: add route topology CI now;
defer generic registry freeze.  
**Confidence:** HIGH.

### CMP-FLASK-003 — Request-local state
**Flask:** AppContext/context proxies.  
**InvoiceIQ:** explicit dependencies + ContextVars.  
**Decision:** KEEP OUR APPROACH.  
**Confidence:** HIGH.

### CMP-FLASK-004 — Session model
**Flask:** signed cookie by default, replaceable interface.  
**InvoiceIQ:** signed JWT + live DB Session + revocation.  
**Decision:** KEEP OUR APPROACH.  
**Confidence:** HIGH.

### CMP-FLASK-005 — Signing key rotation
**Flask:** one current secret + verification fallbacks for session cookies.  
**InvoiceIQ:** one key for access JWT, OIDC state and local keyvault derivation.  
**Decision:** ADAPT WITH PURPOSE SEPARATION.  
**Confidence:** HIGH.

### CMP-FLASK-006 — API compatibility
**Flask:** deprecation wrappers and multi-version compatibility tests.  
**InvoiceIQ:** checked-in OpenAPI drift gate, but no general deprecation policy.  
**Decision:** HYBRID: keep OpenAPI gate; adopt staged-deprecation policy for
public contracts.  
**Confidence:** HIGH.

### CMP-FLASK-007 — Warnings
**Flask:** warnings-as-errors.  
**InvoiceIQ:** blanket DeprecationWarning ignore.  
**Decision:** ADAPT AS A RATCHET, P3.  
**Confidence:** HIGH.

### CMP-FLASK-008 — Dependency matrices
**Flask:** minimum + current + development versions.  
**InvoiceIQ:** exact runtime pins + Dependabot.  
**Decision:** KEEP OUR APPROACH.  
**Confidence:** HIGH.

### CMP-FLASK-009 — App factory
**Flask:** central extension/testing pattern.  
**InvoiceIQ:** singleton module-level app and explicit test resets.  
**Decision:** DEFER/REJECT NOW.  
**Confidence:** MEDIUM-HIGH.

### CMP-FLASK-010 — Error architecture
**Flask:** exception hierarchy and scoped error lookup.  
**InvoiceIQ:** typed domain exceptions + single API mapping boundary.  
**Decision:** KEEP OUR APPROACH.  
**Confidence:** HIGH.

## 9. Engineering debates

### DEBATE FLASK-D1 — Add signing-key fallback support

**Proposal**  
Dedicated `JWT_SIGNING_KEY` and verify-only old key list.

**Support**
- routine key rotation becomes zero-downtime;
- access JWT and OIDC state are the two internal-JWT call sites;
- server-side jti/session remains authoritative.

**Opposition**
- multiple verification keys slightly increase auth complexity;
- retaining an actually compromised key would prolong attacker access.

**Reference evidence**  
Flask supports previous secret keys for cookie signature verification.

**InvoiceIQ evidence**  
The current key also derives local KEK material, so literal copying is unsafe.

**Alternatives**
A. rotate `secret_key` directly;  
B. accept mass logout each routine rotation;  
C. dedicated signing key + verification fallbacks;  
D. migrate immediately to asymmetric JWT/JWKS.

**Cost:** LOW/MEDIUM  
**Risk:** LOW if fallbacks are bounded and verify-only  
**Benefit:** HIGH operationally

**Lead:** **ADAPT C — P2.**

**Emergency rule:** if a key is believed compromised, do not put it in fallback.
Promote a new key immediately and revoke sessions; forced reauthentication is
the correct outcome.

### DEBATE FLASK-D2 — Add route topology guard

**Proposal**  
Fail CI when an earlier dynamic route regex matches a later literal route for a
shared HTTP method.

**Support**
- InvoiceIQ already documents a real shadowing hazard;
- no runtime dependency or architecture change;
- can reuse Starlette's compiled converter-aware regex.

**Opposition**
- route-order behavior is framework-specific;
- unusual intentional shadows might someday exist.

**Alternative**
Continue relying on router comments.

**Cost:** LOW  
**Risk:** LOW  
**Benefit:** MEDIUM/HIGH

**Lead:** **ADOPT PRINCIPLE / IMPLEMENT NATIVELY — P2.**

### DEBATE FLASK-D3 — Warnings as errors

**Proposal**  
Change pytest to fail every warning.

**Support**  
Flask prevents deprecation debt this way.

**Opposition**  
InvoiceIQ currently ignores all DeprecationWarnings. A blind flip may produce a
large third-party failure set unrelated to product correctness.

**Lead:** **ADAPT / P3.** Inventory, fix, exact-allowlist, then enable `error`.

### DEBATE FLASK-D4 — Freeze parser registries after startup

**Proposal**  
Make `ExtractionProvider` and fuel parser registries immutable after setup.

**Support**  
Multi-worker runtime mutation could create different parser order per process.

**Opposition**  
No production code currently calls these registration functions; test mutation
is the observed use. Built-in order is explicit in one module.

**Lead:** **DEFER.** Add freeze only if runtime/plugin registration becomes real.

### DEBATE FLASK-D5 — Refactor to an app factory

**Proposal**  
Replace module singleton FastAPI app with `create_app(settings)`.

**Support**  
Test isolation and multiple configurations become cleaner.

**Opposition**  
Large touch surface, no production need for multiple apps, current tests already
isolate DB/storage/rate-limit state.

**Lead:** **REJECT NOW / P4 revisit only with evidence.**

## 10. Priority backlog

| ID | Priority | Problem | Adaptation | Benefit | Complexity | Risk | Status |
|---|---:|---|---|---|---|---|---|
| FLASK-P2-01 | P2 | JWT rotation coupled to app/local-KEK secret | dedicated current signing key + max 3 verify-only fallbacks | High | Low/Med | Low | PATCH READY |
| FLASK-P2-02 | P2 | literal routes can become unreachable through order | structural route-topology test | Med/High | Low | Low | PATCH READY |
| FLASK-P3-01 | P3 | all DeprecationWarnings hidden | inventory → exact allowlist → warnings-as-errors | Medium | Low/Med | Low | PLAN READY |
| FLASK-P3-02 | P3 | public-contract deprecation discipline not explicit | document compatibility window for public API/provider/automation changes | Medium | Low | Low | DEFER |
| FLASK-P4-01 | P4 | mutable registries could diverge if runtime plugins appear | setup freeze after bootstrap | Medium | Low | Low | FUTURE |
| FLASK-P4-02 | P4 | singleton app limits multi-config testing | app factory only if need appears | Low/Med | High | Med | REJECT NOW |
| FLASK-P4-03 | P4 | dependency forward-compatibility unknown before upgrades | scheduled upstream dependency canary | Low | Medium | Low | FUTURE |

## 11. Lead Developer implementation orders

### TASK FLASK-P2-01 — Separate and rotate internal JWT signing keys

**Objective**  
Allow routine signing-key rotation without rotating the application/local-KEK
secret and without invalidating every active session at one instant.

**Reference lesson**  
Use the current key to sign new artifacts and bounded previous keys only to
verify artifacts minted before promotion.

**Important**  
Do not copy Flask's single secret namespace. InvoiceIQ's `secret_key` has another
cryptographic purpose through the local key vault.

**Current**
- access JWT signs/decodes `settings.secret_key`;
- OIDC state signs/decodes the same;
- local KEK derives SHA-256(secret_key);
- access token lifetime 24h;
- OIDC state lifetime 10m;
- live Session/jti gate is checked after token decode.

**Target**
- `jwt_signing_key: str | None = None`;
- `jwt_signing_key_fallbacks: list[str]`, maximum 3;
- unset current signing key preserves existing behavior;
- new JWTs sign with current signing key;
- access + OIDC state verify current, then old fallbacks;
- old fallbacks are never used for signing;
- production rejects insecure/duplicate/current-equals-fallback configuration;
- no changes to keyvault key derivation.

**Files**
- `backend/app/core/config.py`
- `backend/app/core/security.py`
- `backend/app/services/oidc.py`
- `backend/tests/test_sessions.py`
- `backend/tests/test_sso_oidc.py`

**Rollout**
1. Prefer a production `KEK_KEY` / independent KEK first if still using local
   secret-derived KEK (existing SEC-KEK-001).
2. Introduce `JWT_SIGNING_KEY` equal to current signing material first, or deploy
   future key in fallback while current remains active.
3. Promote new signing key everywhere.
4. Keep old key verify-only for >24h (longest current internal JWT lifetime).
5. Remove old key.
6. For compromise: skip fallback, rotate immediately, revoke sessions.

**Do not change**
- JWT algorithm in this task;
- access-token TTL;
- session/jti model;
- membership/org gates;
- OIDC IdP ID-token JWKS validation;
- local keyvault secret derivation;
- MFA/session-policy backlog.

**Tests**
- new tokens validate with active key;
- old token validates through fallback after promotion;
- new token does NOT validate with retired old-only config;
- removing fallback invalidates old token;
- an old-key access token still dies immediately after its DB session is revoked;
- old OIDC state survives routine promotion while fallback is present;
- it fails after fallback removal;
- tampered state/token still fails;
- production config rejects insecure, duplicate and active-equals-fallback keys;
- full auth + SSO suite.

**Acceptance**
Routine signing-key rotation no longer requires changing `secret_key`; server-side
revocation behavior is unchanged.

**Rollback**
Unset `JWT_SIGNING_KEY` and fallbacks to restore historical single-key behavior.
No DB migration.

### TASK FLASK-P2-02 — Structural route topology gate

**Objective**  
No literal FastAPI endpoint may be silently captured by an earlier parameterized
route with the same method.

**Reference lesson**  
Registration topology is setup-time state and should be validated/frozen before
runtime.

**Current**  
A comment manually enforces recurring-before-issued due a known shadow risk.

**Target**
A CI test inspects final `app.routes`:
- only APIRoutes;
- later literal routes;
- earlier parameter routes sharing methods;
- use earlier route's compiled `path_regex.fullmatch(literal_path)`;
- fail with both route strings and methods.

**File**
- new `backend/tests/test_route_topology.py`

**Do not change**
- APIRouter architecture;
- route ordering merely to satisfy hypothetical cases;
- OpenAPI snapshot gate.

**Tests**
- actual app has zero shadow failures;
- self-test creates known bad mini FastAPI app and proves detector fires;
- self-test creates correct order and proves no false positive.

**Acceptance**
A future accidental reorder that makes a literal route unreachable fails CI
before merge.

**Rollback**
Remove test; runtime unaffected.

## 12. Cross-repository synthesis

Flask reinforces several patterns already seen elsewhere:

- **Paperless / PSC / Stirling / Flask:** immutable CI action refs are not a
  repository-specific style; they are a durable supply-chain pattern.
- **Twenty / Flask:** registration/capability metadata should be explicit, but
  InvoiceIQ should keep a bounded financial action catalog rather than a generic
  extension graph.
- **Stirling / Flask:** configuration belongs to explicit policy objects rather
  than scattered literals; InvoiceIQ Pydantic Settings is the stronger native
  implementation.
- **Scrapling / Flask:** request/work context must have explicit lifetime; pass
  necessary data to asynchronous work rather than smuggling ambient request
  context across process boundaries.
- **Flask + InvoiceIQ:** framework/library compatibility bridges are useful, but
  the OpenAPI snapshot and stable AppError codes remain the stronger
  application-facing contracts for InvoiceIQ.

## 13. Final Lead answer

The two changes that make InvoiceIQ measurably better without unnecessary
complexity are:

```text
1. APPLICATION SECRET / LOCAL KEK
           |
        remains stable

   JWT SIGNING PURPOSE
      current key
          +
   bounded old verify-only keys
          |
   access JWT + OIDC state
          |
   existing live-session/jti gate
```

and:

```text
FastAPI route tree
       |
structural topology test
       |
no dynamic route may intercept
a later literal route
       |
OpenAPI drift gate remains
```

These are small, native adaptations of Flask principles. They do not turn
InvoiceIQ into Flask and do not add a plugin framework, proxy-global layer,
cookie-session model, WSGI runtime or app-factory architecture.
