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


---

# Paperless-ngx observations — 2026-09-06

## PAT-018 — Ordered preprocessing lifecycle
**Observed in:** Paperless consume plugins  
**Purpose:** optional ingest steps have explicit setup/run/cleanup and can halt safely.  
**InvoiceIQ:** CONDITIONALLY. Introduce only when real capture steps begin duplicating orchestration.

## PAT-019 — Immutable source + disposable working copy
**Observed in:** Paperless consumer; InvoiceIQ content-addressed object storage  
**Purpose:** untrusted/expensive tools never mutate the source of truth.  
**InvoiceIQ:** YES — already stronger via immutable source storage.

## PAT-020 — Conditional derivative generation
**Observed in:** Paperless archive decision  
**Purpose:** produce expensive thumbnails/renditions only when they improve compatibility/review.  
**InvoiceIQ:** YES WHEN DOCUMENT UX IS BUILT.

## PAT-021 — Background execution correlation context
**Observed in:** Paperless `consume_task_id` ContextVar  
**Purpose:** logs across services remain tied to one task.  
**InvoiceIQ:** YES — add job ID/kind context.

## PAT-022 — Connect-time DNS pinning
**Observed in:** Paperless pinned httpx transport  
**Purpose:** make SSRF validation govern the actual socket destination, not an earlier DNS answer.  
**InvoiceIQ:** YES for outbound user-configurable URLs.

## PAT-023 — Secondary-index permission enforcement
**Observed in:** Paperless Tantivy query filtering  
**Purpose:** external search indexes enforce visibility before returning IDs.  
**InvoiceIQ:** REQUIRED RULE for any future FTS/vector index.

## PAT-024 — Rebuildable projections fail independently
**Observed in:** Paperless search-index retry/rebuild  
**Purpose:** search/index failure must not invalidate authoritative records.  
**InvoiceIQ:** YES as a future projection rule.

## PAT-025 — Operational “needs attention” inbox
**Observed in:** Paperless task UI  
**Purpose:** failures/in-progress/completed work are visible and filterable.  
**InvoiceIQ:** CONDITIONAL; likely valuable for self-service job/capture recovery.

## PAT-026 — Immutable CI action dependencies
**Observed in:** Paperless GitHub workflow SHA pins  
**Purpose:** CI behavior cannot change because a mutable action tag moved.  
**InvoiceIQ:** YES.

## PAT-027 — Protected production branch
**Observed in:** Paperless protected dev branch; InvoiceIQ main currently unprotected  
**Purpose:** production changes must pass declared gates.  
**InvoiceIQ:** YES, repository setting.

## PAT-028 — Source-code + workflow static analysis
**Observed in:** Paperless Semgrep + zizmor  
**Purpose:** security scan application code and CI configuration separately.  
**InvoiceIQ:** EVALUATE NON-BLOCKING BEFORE ENFORCEMENT.

## PAT-029 — Resource oversubscription guard
**Observed in:** Paperless `OMP_THREAD_LIMIT=1`  
**Purpose:** worker-level concurrency should not be multiplied unpredictably inside CPU libraries.  
**InvoiceIQ:** BENCHMARK before adoption.

## PAT-030 — Workflow metadata through canonical ingest
**Observed in:** Paperless consume-time workflow overrides  
**Purpose:** automation modifies canonical input/context rather than forking the ingest implementation.  
**InvoiceIQ:** YES PRINCIPLE; keep bounded automation.

## PAT-031 — Parser capability attribution
**Observed in:** Paperless parser registry logs parser identity/version/author  
**Purpose:** operators can explain which implementation handled a document.  
**InvoiceIQ:** YES principle; provider provenance already recorded. Do not adopt arbitrary entrypoints.

## PAT-032 — Document versions are not accounting revisions
**Observed in:** Paperless root/version chain  
**Purpose:** general DMS file history.  
**InvoiceIQ:** NO for booked financial records; corrections remain domain events.

## PAT-033 — Full-text OCR index as optional projection
**Observed in:** Paperless Tantivy  
**Purpose:** fast archive content search.  
**InvoiceIQ:** FUTURE only after PostgreSQL FTS benchmark and a proven user need.

## PAT-034 — Path-aware CI
**Observed in:** Paperless change detection  
**Purpose:** skip irrelevant expensive test matrices.  
**InvoiceIQ:** CONDITIONAL; only after proving no cross-layer gate can be silently skipped.

## Paperless rejected-pattern additions

### NO-009 — Arbitrary Python parser plugins in a multi-tenant SaaS
Third-party executable code is not an acceptable tenant extension mechanism.

### NO-010 — Generic soft-delete/trash for statutory records
Retention/deletion semantics must remain domain-specific.

### NO-011 — Redis/WebSockets just for progress animation
Persisted truthful progress is enough at current scale.

### NO-012 — Search index as authority
PostgreSQL/object storage remain canonical; any index is rebuildable.

### NO-013 — Accounting correction as document version replacement
Use credit notes/revised business records and immutable evidence.


---

# Twenty observations — 2026-09-06

## PAT-035 — Plan before structural mutation
**Observed in:** Twenty app metadata `plan` / sync flow  
**Purpose:** calculate create/update/destroy consequences before mutation.  
**InvoiceIQ:** YES principle; use explicit migration impact + fail-closed data preflight, not a metadata engine.

## PAT-036 — Stable identifiers for evolvable definitions
**Observed in:** Twenty application metadata universal identifiers  
**Purpose:** references survive renames/environment-specific database IDs.  
**InvoiceIQ:** CONDITIONALLY for future configurable/plugin definitions.

## PAT-037 — One source of truth for action/navigation availability
**Observed in:** Twenty command menu; InvoiceIQ permission/module-filtered nav  
**Purpose:** sidebar, keyboard navigation and contextual actions must not drift into different authorization/UI rules.  
**InvoiceIQ:** YES — command palette derives only from filtered `navGroups`.

## PAT-038 — Saved worklist configuration
**Observed in:** Twenty Views  
**Purpose:** operators retain filters/sorts/columns without changing underlying records.  
**InvoiceIQ:** CONDITIONALLY; validate repeated invoice/claim workflows first.

## PAT-039 — Agent as an attributable actor
**Observed in:** Twenty `AgentActorContextService`  
**Purpose:** AI actions have a workspace, identity, source and audit attribution.  
**InvoiceIQ:** REQUIRED before write-capable agents.

## PAT-040 — Agent role + run-as role
**Observed in:** Twenty AI execution  
**Purpose:** agent capability is permission-scoped rather than implicitly superuser.  
**InvoiceIQ:** REQUIRED; effective access must never exceed either safety boundary.

## PAT-041 — Default-deny agent tool catalog
**Observed in:** Twenty tool registry / role-filtered catalogs  
**Purpose:** the model can call only explicitly exposed tools.  
**InvoiceIQ:** REQUIRED before write/action tools.

## PAT-042 — Lazy tool discovery after tool count grows
**Observed in:** Twenty `learn_tools` / `execute_tool` meta-tool strategy  
**Purpose:** avoid loading every tool schema into open-ended agent prompts.  
**InvoiceIQ:** FUTURE; measure first.

## PAT-043 — Agent recursion guard
**Observed in:** Twenty exclusion of workflow registry/navigation tool classes  
**Purpose:** prevent circular workflow/agent execution and runaway loops.  
**InvoiceIQ:** REQUIRED if agents can invoke automation.

## PAT-044 — AI spend and step ceilings
**Observed in:** Twenty AI billing + max-step stop conditions  
**Purpose:** bound financial and computational blast radius.  
**InvoiceIQ:** REQUIRED for paid/generative runtime actions.

## PAT-045 — Tool-level telemetry
**Observed in:** Twenty agent/tool metrics  
**Purpose:** diagnose agent systems by tool latency/error/cost, not only HTTP latency.  
**InvoiceIQ:** REQUIRED when agent tooling grows.

## PAT-046 — Connection-level outbound network policy
**Observed in:** Paperless + Twenty  
**Purpose:** SSRF decisions govern the socket actually opened, including rebinding/redirects.  
**InvoiceIQ:** YES; reinforces the prepared Paperless hardening.

## PAT-047 — Secrets stay server-side across extension boundaries
**Observed in:** Twenty front components / logic functions  
**Purpose:** custom/browser code receives user-scoped access while privileged secrets remain server-side.  
**InvoiceIQ:** FUTURE extension security law.

## PAT-048 — Custom code is a separate isolation problem
**Observed in:** Twenty logic-function driver/child-process execution  
**Purpose:** tenant/app code is not ordinary business-service code.  
**InvoiceIQ:** NO tenant code today; preserve declarative automation.

## PAT-049 — Semantic API compatibility gate
**Observed in:** Twenty PR-vs-main GraphQL/OpenAPI comparison  
**Purpose:** distinguish intentional additive API evolution from breaking changes.  
**InvoiceIQ:** P3 after public API compatibility becomes contractual; snapshot drift already catches changes.

## PAT-050 — Selective production-parity tests
**Observed in:** Twenty cross-repository prod-parity E2E dispatch  
**Purpose:** expensive integration environments run where signal justifies cost.  
**InvoiceIQ:** CONDITIONAL.

## PAT-051 — Metadata-driven physical schemas are product-specific
**Observed in:** Twenty per-workspace PostgreSQL schema + generated GraphQL  
**Purpose:** arbitrary tenant objects/fields.  
**InvoiceIQ:** NO for core finance. Keep shared schema + RLS.

## PAT-052 — Database invariant beats workflow convention
**Observed in:** Twenty unique active workflow-version constraint; InvoiceIQ's financial CHECK/FK work  
**Purpose:** states/concurrency guarantees live as close to data as practical.  
**InvoiceIQ:** YES, already core audit principle.

## PAT-053 — Dependency exception has an expiry story
**Observed in:** Twenty dependency resolutions; InvoiceIQ pip-audit ignore rationale  
**Purpose:** pin/ignore is a documented temporary engineering decision, not unexplained permanent debt.  
**InvoiceIQ:** YES.

## PAT-054 — Don't package a design system before a second consumer exists
**Observed in contrast:** Twenty shared UI package vs InvoiceIQ in-app design system  
**Purpose:** avoid monorepo/package complexity without reuse pressure.  
**InvoiceIQ:** KEEP current component boundary until another app genuinely consumes it.

## New rejected-pattern additions

### NO-014 — Dynamic metadata objects for financial core
Configurable CRM records are not a substitute for typed tax/payment/invoice semantics.

### NO-015 — Per-tenant physical schema without per-tenant schema requirements
RLS/shared schema remains simpler and more operable for InvoiceIQ.

### NO-016 — Dynamic GraphQL as flexibility theater
Do not replace explicit REST/Pydantic contracts when the domain itself is stable.

### NO-017 — Tenant-authored executable logic before a product requirement
Sandboxing, dependency layers, secrets, networking and resource limits are a new product/security domain.

### NO-018 — Redis/BullMQ because a large reference repo uses it
Queue technology follows measured scale/coordination needs.

### NO-019 — Permission DSL replacing domain capabilities
Fine-grained generic field/row permissions can obscure financial authority. Keep explicit capability names + RLS.

### NO-020 — Write-capable AI with implicit user authority
An LLM must never inherit broad tenant access merely because a human opened the chat.


---

# Personal Security Checklist observations — 2026-09-06

## PAT-055 — Security controls need stable identity
**Observed in contrast:** PSC checklist progress vs InvoiceIQ control needs.  
**Purpose:** wording/labels can change without changing the underlying control.  
**InvoiceIQ:** REQUIRED; IDs such as `SEC-SC-001` are immutable.

## PAT-056 — Security status is more than complete/incomplete
**Observed in:** PSC complete/ignored distinction.  
**Purpose:** represent `verified`, `open`, `blocked`, `deferred`, `not_applicable` honestly.  
**InvoiceIQ:** YES; every non-applicable item needs rationale, every verified item needs evidence.

## PAT-057 — Machine-readable security register + generated human view
**Observed in:** PSC YAML → Markdown generator.  
**Purpose:** keep structured posture and readable documentation synchronized.  
**InvoiceIQ:** YES, but CI checks drift; no self-commit.

## PAT-058 — Evidence is part of a security change
**Observed in:** PSC PR/issue templates.  
**Purpose:** security claims must be reviewable, sourced and reproducible.  
**InvoiceIQ:** YES; tests/evidence/impact/rollback are explicit PR fields.

## PAT-059 — AI contribution disclosure without AI exception
**Observed in:** PSC PR template.  
**Purpose:** reviewer knows when generated work needs independent verification.  
**InvoiceIQ:** YES. AI assistance never relaxes testing or review.

## PAT-060 — GitHub Action SHA pinning
**Observed in:** PSC CI.  
**Purpose:** workflow dependency is immutable and review corresponds to exact executable code.  
**InvoiceIQ:** P1 immediate.

## PAT-061 — Disable checkout credential persistence by default
**Observed in:** PSC CI.  
**Purpose:** least privilege across later workflow steps/actions.  
**InvoiceIQ:** YES except intentional repository-writing jobs.

## PAT-062 — Workflow lint is a security gate
**Observed in:** actionlint + zizmor.  
**Purpose:** application tests do not validate workflow token/expression/permission safety.  
**InvoiceIQ:** YES.

## PAT-063 — PII scan and secret scan are orthogonal
**Observed in contrast:** InvoiceIQ PII scanner vs PSC TruffleHog.  
**Purpose:** customer identifiers and cloud/API credentials have different detection models.  
**InvoiceIQ:** run both.

## PAT-064 — Dependency introduction deserves PR-time security review
**Observed in:** PSC dependency-review action.  
**Purpose:** stop known-vulnerable dependency changes before merge.  
**InvoiceIQ:** YES alongside pip-audit/npm/Dependabot.

## PAT-065 — Supply-chain tools are themselves supply-chain dependencies
**Observed in:** PSC pins actionlint/zizmor/TruffleHog/dependency-review actions.  
**Purpose:** a security scanner referenced by mutable tag can itself become the compromise path.  
**InvoiceIQ:** REQUIRED.

## PAT-066 — Generated artifacts should fail on drift, not self-bless
**Observed in contrast:** PSC auto-commit vs InvoiceIQ OpenAPI gate.  
**Purpose:** review remains the approval boundary.  
**InvoiceIQ:** KEEP OUR STRONGER MODEL.

## PAT-067 — Repository security policy is production surface
**Observed in:** InvoiceIQ placeholder `SECURITY.md`.  
**Purpose:** researchers need a truthful private disclosure route and response expectations.  
**InvoiceIQ:** P1 owner decision.

## PAT-068 — Local-first state is a privacy technique, not an assurance technique
**Observed in:** PSC browser-only progress.  
**Purpose:** collect less data when durability/audit is unnecessary.  
**InvoiceIQ:** use for optional preferences where appropriate; not financial/security control truth.

## PAT-069 — Build output should be asserted
**Observed in:** PSC static artifact verification.  
**Purpose:** a successful compiler process does not prove required deliverables exist.  
**InvoiceIQ:** already stronger through container/E2E/deploy health; keep.

## PAT-070 — Update cadence should match product risk
**Observed in contrast:** PSC monthly/cooldown vs InvoiceIQ weekly.  
**Purpose:** copy the reviewability principle, not a repository's schedule.  
**InvoiceIQ:** KEEP weekly grouped updates.

## PAT-071 — Static architecture is a valid attack-surface reduction when product permits
**Observed in:** PSC SSG site.  
**Purpose:** eliminate server state/code where content delivery does not require it.  
**InvoiceIQ:** use for public docs/marketing only.

## PAT-072 — “Security project” is not a security control
**Observed in:** PSC default branch unprotected.  
**Purpose:** judge implementations and repository policy, not stars/topic/reputation.  
**InvoiceIQ:** reinforces anti-cargo-cult rule.

## PAT-073 — Documentation capability claims need executable evidence
**Observed in:** PSC README API claim vs empty/absent visible implementation.  
**Purpose:** prevent roadmap/stale docs from becoming false product promises.  
**InvoiceIQ:** continue OpenAPI/route/structural gates.

## PAT-074 — Authentication recommendations require current implementation standards
**Observed in:** PSC MFA/passkey guidance.  
**Purpose:** high-level advice is useful for priority but not enough for secure protocol design.  
**InvoiceIQ:** use OWASP/NIST/WebAuthn implementation requirements.

## New rejected-pattern additions

### NO-021 — Browser-local “security compliance”
A local checkbox is user progress, not auditable control evidence.

### NO-022 — Display text as control primary key
Renames must not orphan history/evidence.

### NO-023 — Generated workflow committing directly to protected/default branch
CI may propose or verify generated output; humans/normal merge governance approve it.

### NO-024 — Copy personal security advice into SaaS requirements verbatim
Map threats to our system first.

### NO-025 — No tests because a product is content-heavy
Lint/type/build are useful but not regression proof.

### NO-026 — Slower dependency updates because a reference does it
Update cadence is context-dependent.

### NO-027 — Placeholder API/infrastructure files
Unimplemented capability should be clearly absent or explicitly experimental.

### NO-028 — Security-by-reputation
Popularity/security branding never substitutes for evidence.

---

# Lago observations — 2026-09-06

## PAT-075 — Acknowledge provider delivery only after durable ownership
**Observed in:** Lago inbound webhook persistence + async processing.  
**Purpose:** transient business/database failure must not turn a valid provider event into silent loss.  
**InvoiceIQ:** ADOPT using existing Job row, not a new webhook table.

## PAT-076 — Immutable external financial operation identity
**Observed in:** Lago event transaction IDs; reinforced by InvoiceIQ EveryPay MIT.  
**Purpose:** a retry repeats the same semantic effect, not a freshly computed larger/different effect.  
**InvoiceIQ:** REQUIRED for Stripe metered-usage segments.

## PAT-077 — Atomic batch semantics are part of the API contract
**Observed in:** Lago batch event service.  
**Purpose:** clients know whether a batch is all-or-nothing vs partial.  
**InvoiceIQ:** audit every bulk financial endpoint/import and document/test semantics.

## PAT-078 — Separate lifecycle, payment and tax dimensions
**Observed in:** Lago Invoice.  
**Purpose:** prevent overloaded status contradictions.  
**InvoiceIQ:** already follows the principle; keep.

## PAT-079 — Queue topology is a deployment choice, not a domain dependency
**Observed in:** Lago optional dedicated Sidekiq workers.  
**Purpose:** start simple, isolate expensive classes when scale proves need.  
**InvoiceIQ:** use existing `kinds`/`exclude`; no Redis migration.

## PAT-080 — Scheduled reconciliation complements immediate retry
**Observed in:** Lago Clockwork retry/recovery jobs.  
**Purpose:** recover work that escaped normal execution/retry paths.  
**InvoiceIQ:** stale lease reclaim + daily scheduler already provide framework; add domain recovery only for evidence-backed gaps.

## PAT-081 — Scenario tests target business timelines
**Observed in:** Lago `spec/scenarios`.  
**Purpose:** billing bugs often live between services and time boundaries rather than inside one function.  
**InvoiceIQ:** add lost-response scenarios for provider boundaries.

## PAT-082 — Pricing-model compatibility should fail at configuration time
**Observed in:** Lago Charge/BillableMetric validations.  
**Purpose:** invalid billing combinations never reach invoice runtime.  
**InvoiceIQ:** conditionally apply if dynamic pricing/catalog is introduced.

## PAT-083 — Current usage is internal source of truth; provider is a projection
**Observed in:** Lago metering architecture; aligned with InvoiceIQ UsageCounter.  
**Purpose:** switching payment/billing provider must not erase entitlement/meter truth.  
**InvoiceIQ:** KEEP.

## PAT-084 — Deferred constraints need an expiry date
**Observed in:** Lago AGENTS migration rule.  
**Purpose:** NOT VALID must not become permanent integrity debt.  
**InvoiceIQ:** add only when first deferred validation is used.

## PAT-085 — Agent rules should name exact repository invariants and commands
**Observed in:** Lago API AGENTS.md.  
**Purpose:** AI/new contributors execute the same architecture/test expectations as humans.  
**InvoiceIQ:** cross-link `engineering-rules.md` into future agent contract; avoid duplicate rulebooks.

## PAT-086 — Provider failure is domain data, not just a log line
**Observed in:** Lago provider failure/result classes + webhook/inbound state.  
**Purpose:** operators can distinguish retryable integration failure from business no-op.  
**InvoiceIQ:** durable Job status/DLQ is sufficient for Stripe subscription application once routed through jobs.

## PAT-087 — Freeze outbound quantity before an at-least-once side effect
**Cross-repo synthesis:** Lago event identity + InvoiceIQ EveryPay MIT.  
**Purpose:** accepted-response-lost cannot expand a retry.  
**InvoiceIQ:** `UsageCounter.reporting_target`.

## New rejected-pattern additions

### NO-029 — Introducing Redis/Sidekiq solely to imitate a billing reference
Existing DB queue already solves required durability and lane isolation.

### NO-030 — Introducing Kafka before measured event-ingestion scale needs it
A broker is not an idempotency mechanism.

### NO-031 — Generalized rating engine before a dynamic pricing product exists
Configuration power creates validation, migration and UX surface area.

### NO-032 — Copying AGPL implementation code into InvoiceIQ
Use ideas; independently implement native code.

### NO-033 — Splitting a working monorepo into submodules because a reference does
InvoiceIQ benefits from atomic cross-stack changes.

### NO-034 — New receipt table when an existing durable job is the exact work record required
Avoid duplicate state machines.

### NO-035 — One status column for lifecycle + settlement + delivery
InvoiceIQ already avoids this; never regress.

### NO-036 — Treating provider HTTP success as local transactional success
External acceptance and local acknowledgement are separate failure domains.

---

# DuckDB observations — 2026-09-07

## PAT-088 — Push selection, projection and limit to the data engine
**Observed in:** DuckDB filter pushdown + unused-column removal.  
**Purpose:** avoid carrying/materializing data that will be discarded.  
**InvoiceIQ:** YES — dashboard AP inbox is a measured example.

## PAT-089 — Keep logical semantics stable while optimizing physical execution
**Observed in:** parser/planner/logical plan vs physical operator pipeline.  
**Purpose:** performance work should not fork domain definitions.  
**InvoiceIQ:** YES — preserve canonical `waiting_for` semantics, change query shape only.

## PAT-090 — Performance artifacts should attribute work
**Observed in:** QueryProfiler operator tree, query/phase/IO metrics.  
**Purpose:** know why a request is slow, not only that it is slow.  
**InvoiceIQ:** CONDITIONALLY — targeted SQL/query-count/EXPLAIN artifacts for diagnosed hotspots.

## PAT-091 — Benchmark optimizations with the workload they claim to improve
**Observed in:** micro optimizer/pushdown benchmarks and benchmark runner.  
**Purpose:** an optimization without measurement is a hypothesis.  
**InvoiceIQ:** YES — PERF-DUCK-001 must rerun dashboard concurrency.

## PAT-092 — Same correctness contract under alternate execution configurations
**Observed in:** DuckDB `test_configs` matrix.  
**Purpose:** reveal hidden dependencies on optimizer/storage/execution choices.  
**InvoiceIQ:** CONDITIONALLY — only for real alternate modes.

## PAT-093 — Fuzz the actual parser/file boundary
**Observed in:** SQL, CSV/JSON and Parquet fuzz harnesses.  
**Purpose:** random input must reach the subsystem under risk, not stop at a generic parser.  
**InvoiceIQ:** CONDITIONALLY — external document/structured payload paths.

## PAT-094 — Promote fuzz discoveries into deterministic regressions
**Observed in:** DuckDB fuzzer/AFL regression tests.  
**Purpose:** a found bug never depends on the fuzzer finding it twice.  
**InvoiceIQ:** YES for any future fuzz campaign.

## PAT-095 — Encode easy-to-confuse invariants in distinct types/closed APIs
**Observed in:** `VisibilityBound` at current DuckDB head.  
**Purpose:** make invalid semantic combinations hard/impossible to express.  
**InvoiceIQ:** CONDITIONALLY; already strong with enums, Decimal, CHECKs and composite tenant keys.

## PAT-096 — Validate task/pipeline dependency graphs before execution
**Observed in:** DuckDB Executor event/pipeline DAG verification.  
**Purpose:** fail on orchestration cycles before runtime deadlock/incorrect order.  
**InvoiceIQ:** FUTURE only if automation becomes a real DAG.

## PAT-097 — Executable extension provenance is a security boundary
**Observed in:** DuckDB extension-signature verifier.  
**Purpose:** executing optional third-party code requires authenticity/provenance.  
**InvoiceIQ:** FUTURE; no executable plugin system today.

## PAT-098 — Agent instructions should explicitly forbid legacy/deprecated implementation patterns
**Observed in:** DuckDB AGENTS vector API guidance.  
**Purpose:** agents need both the preferred pattern and the old pattern they must not reintroduce.  
**InvoiceIQ:** YES; extend/cross-link existing engineering rules, don't duplicate.

## PAT-099 — Batching reduces abstraction-crossing cost
**Observed in:** vectorized DataChunks.  
**Purpose:** amortize per-item overhead for analytical/bulk work.  
**InvoiceIQ:** YES principle for exports/imports/analytics, not a vector-engine implementation.

## New rejected patterns

### NO-037 — Replacing the transactional source of truth with an embedded OLAP engine
PostgreSQL remains the correct InvoiceIQ authoritative database.

### NO-038 — Building a query optimizer inside application code
Use PostgreSQL's optimizer; write better set-based queries.

### NO-039 — Caching a financial dashboard before eliminating obvious wasted work
Pushdown first, benchmark, then reconsider caching with evidence.

### NO-040 — Parallelizing AsyncSession queries to imitate analytical pipelines
One session is not a parallel execution engine; multiple sessions weaken the snapshot and increase pressure.

### NO-041 — Adding a second analytical database before Postgres is measurably inadequate
Operational complexity must buy a proven benefit.

### NO-042 — Adding indexes from architectural intuition alone
Use EXPLAIN/benchmark evidence.

### NO-043 — Broad primitive-wrapper refactor without an evidenced semantic-confusion bug
Strong types are useful when they prevent a real class of mistakes; otherwise they are churn.

### NO-044 — Large alternate-configuration CI matrix for modes the product never runs
Test genuine runtime choices, not sophistication.

### NO-045 — Copying C++ ownership/vector abstractions into Python
Transfer invariant and batching principles only.
