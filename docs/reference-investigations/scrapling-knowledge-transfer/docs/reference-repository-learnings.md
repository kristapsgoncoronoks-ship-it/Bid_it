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
