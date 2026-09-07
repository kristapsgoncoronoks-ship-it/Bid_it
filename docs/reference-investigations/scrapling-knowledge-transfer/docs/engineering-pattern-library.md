# Engineering Pattern Library

InvoiceIQ's cumulative cross-repository engineering playbook.

## Rules

1. Append lessons; do not overwrite prior repository evidence.
2. A pattern is not approved because a respected repository uses it.
3. Every adoption must name the InvoiceIQ problem and evidence that it exists.
4. Prefer the engineering principle over copied code.
5. Keep rejected patterns so future agents do not repeatedly propose them.
6. Financial correctness, tenant isolation, auditability and deterministic behavior outrank architectural novelty.

---

## PAT-001 — Context-owned adapter seam

**Observed in:** InvoiceIQ, Scrapling  
**Purpose:** isolate external implementations behind stable domain contracts.

**Advantages:** extensibility, testability, stable callers.  
**Risk:** a universal interface can erase useful domain semantics.

**InvoiceIQ decision:** **YES — already implemented.**

Current forms:
- `ExtractionProvider`
- `BillingProvider`
- storage backend Protocol
- fuel-card parser registry
- ERP exporter registry
- email adapters
- SSO connections

**Rule:** add an adapter to the bounded context it feeds. Do not build a generic "integrations" domain.

---

## PAT-002 — Durable work identity / idempotency

**Observed in:** Scrapling request fingerprint; InvoiceIQ job idempotency keys.  
**Purpose:** recognize the same logical work across retries/concurrency.

**InvoiceIQ decision:** **YES, DB-backed.**

**Rule:** canonicalize the natural identity, then enforce it with a database uniqueness constraint where outcomes matter.

Never depend only on:
- pre-SELECT dedupe;
- process-local `seen` sets;
- timestamps/random IDs as business idempotency.

---

## PAT-003 — In-flight work is recovery state

**Observed in:** Scrapling scheduler snapshots; InvoiceIQ leased jobs.  
**Purpose:** process death must not silently lose executing work.

**InvoiceIQ decision:** **YES — existing lease/reclaim model is authoritative.**

---

## PAT-004 — Atomic replace for filesystem tooling

**Observed in:** Scrapling checkpoint/cache.  
**Purpose:** avoid half-written local recovery/replay artifacts.

**InvoiceIQ decision:** **CONDITIONALLY.**

Good:
- developer caches;
- generated artifacts;
- fixture generation.

Not a substitute for:
- DB transactions;
- audited business state;
- object-storage integrity.

---

## PAT-005 — Replayable external input

**Observed in:** Scrapling development response cache.  
**Purpose:** make external-input logic reproducible during development.

**InvoiceIQ decision:** **YES for test fixtures; NO as a generic production cache.**

Target:
- sanitized AP extraction replay corpus;
- expected normalized output;
- PII scan required.

---

## PAT-006 — Adaptive drift memory

**Observed in:** Scrapling adaptive selector memory; InvoiceIQ transport extraction baseline.  
**Purpose:** detect when a parser no longer reproduces known-good interpretation.

**InvoiceIQ decision:** **YES, ADVISORY ONLY.**

For financial facts:
- detect;
- explain;
- human confirms;
- rebaseline is explicit and audited;
- never fuzzy-auto-write money, tax identity or bank details.

---

## PAT-007 — Receiver-directed backpressure

**Observed in:** Scrapling AutoThrottle / `Retry-After`.  
**Purpose:** avoid retrying a recovering dependency before it asks.

**InvoiceIQ decision:** **YES.**

Rule:

```text
effective_retry_delay =
    max(local_exponential_backoff, valid_downstream_retry_after)
```

A receiver can lengthen our wait; it cannot make our queue less defensive.

---

## PAT-008 — Bound production at task creation

**Observed in:** Scrapling CrawlerEngine.  
**Purpose:** prevent thousands of waiting coroutine tasks from becoming hidden memory/latency load.

**InvoiceIQ decision:** **YES as a future fan-out invariant.**

Current DB worker is sequential and does not need rework.

---

## PAT-009 — Dedicated workload lanes

**Observed in:** Scrapling domain/session limits; InvoiceIQ worker `kinds`/`exclude`.  
**Purpose:** expensive work cannot starve cheap work.

**InvoiceIQ decision:** **YES — existing lane model.**

Before increasing concurrency:
- measure lane workload;
- set explicit resource ceiling;
- prove DB/provider capacity.

---

## PAT-010 — Closed-loop concurrency

**Observed in:** Scrapling AutoThrottle.  
**Purpose:** adapt load to observed service health.

**InvoiceIQ decision:** **CONDITIONALLY.**

Good targets:
- external APIs;
- provider 429/503;
- future OCR/AI APIs.

Bad default:
- silently throttling ordinary user-facing DB reads instead of fixing/querying the actual bottleneck.

---

## PAT-011 — Execution statistics as an operational contract

**Observed in:** Scrapling CrawlStats; InvoiceIQ queue health/Prometheus.  
**Purpose:** long-running systems expose workload shape and failure state.

**InvoiceIQ decision:** **YES — current queue SLOs are stronger for production.**

Possible future:
- per-job-kind attempt count;
- duration histogram;
- provider status distribution.

---

## PAT-012 — Cheap deterministic path before expensive fallback

**Observed in:** Scrapling static → browser → stealth; InvoiceIQ deterministic extraction → OCR → future optional AI.  
**Purpose:** reduce cost, latency, uncertainty and external dependencies.

**InvoiceIQ decision:** **YES — core principle.**

---

## PAT-013 — Optional capability packaging

**Observed in:** Scrapling extras/lazy imports.  
**Purpose:** deployments do not pay for unused heavy capabilities.

**InvoiceIQ decision:** **CONDITIONALLY / FUTURE.**

Only split worker images/dependencies when measured operational benefit exceeds deployment complexity.

---

## PAT-014 — Behavioral invariant tests

**Observed in:** both repositories.  
**Purpose:** prove failure semantics, not only happy outputs.

**InvoiceIQ decision:** **YES, REQUIRED.**

Examples:
- RLS cannot leak;
- duplicate enqueue cannot duplicate outcome;
- Retry-After cannot shorten backoff;
- money never crosses currencies;
- migration/model cannot drift;
- worker death cannot hot-loop forever.

---

## PAT-015 — Complete diagnostic CI fan-in

**Observed in:** Scrapling code-quality workflow.  
**Purpose:** one run reports every independent static failure.

**InvoiceIQ decision:** **CONDITIONALLY.**

Adopt if it materially shortens developer feedback. Never convert a hard gate into a warning.

---

## PAT-016 — AI-assisted change provenance

**Observed in:** Scrapling AI contribution policy; InvoiceIQ commit session metadata.  
**Purpose:** AI speed does not erase accountability.

**InvoiceIQ decision:** **YES.**

Required:
- provenance;
- human/Lead understanding;
- evidence;
- no invented product/legal/tax policy;
- invariant review;
- external-code license attribution.

---

## PAT-017 — Agent-facing operating contract

**Observed in:** Scrapling official skill.  
**Purpose:** AI tools receive stable operational/security guidance.

**InvoiceIQ decision:** **FUTURE, when product tools are agent-accessible.**

Contract must cover:
- tenant/permission boundaries;
- no invented financial facts;
- untrusted-content/prompt-injection handling;
- confirmation for consequential mutations;
- audit expectations.

---

# Rejected pattern register

## NO-001 — Process-local queue for financial background jobs
Use the durable DB queue.

## NO-002 — Pickled workflow state as financial source of truth
Use transactional schema/object storage.

## NO-003 — Automatic fuzzy financial-field substitution
Use advisory drift + human confirmation.

## NO-004 — Universal integration manager
Keep context-owned adapters.

## NO-005 — Horizontal/in-process concurrency by default
Measure and constrain the actual workload first.

## NO-006 — Add a tool because a respected repository uses it
Run a signal/false-positive comparison first.

## NO-007 — Multi-runtime CI for a single-runtime deployed application
Add only if portability becomes a real requirement.

## NO-008 — Production filesystem cache for durable finance input
Use object storage/database; replay belongs to development/tests.

---

# Repository observation index

## D4Vinci/Scrapling — 2026-09-06

Reference commit: `28c329671485daaea89a40fb34a7db8622e51468`

Patterns observed:
- PAT-001
- PAT-002
- PAT-003
- PAT-004
- PAT-005
- PAT-006
- PAT-007
- PAT-008
- PAT-010
- PAT-011
- PAT-012
- PAT-013
- PAT-014
- PAT-015
- PAT-016
- PAT-017

Adopted immediately:
- PAT-007 — Retry-After adaptation
- PAT-016 — AI engineering governance
- the permanent pattern-library process itself

Already implemented better/in the right context:
- PAT-001
- PAT-002
- PAT-003
- PAT-006 for transport
- PAT-009
- PAT-011
- PAT-012
- PAT-014

Deferred:
- PAT-005 AP replay implementation
- AP extension of PAT-006
- PAT-010 wider adaptive concurrency
- PAT-013 optional worker profiles
- PAT-015 CI fan-in
- PAT-017 agent skill

Rejected direct implementations:
- Scrapling scheduler/checkpoint stack;
- automatic fuzzy relocation for finance;
- crawler/stealth/browser stack;
- Spider inheritance for business workflows.
