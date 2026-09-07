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
