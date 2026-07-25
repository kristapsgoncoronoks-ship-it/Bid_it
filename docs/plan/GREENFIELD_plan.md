# GREENFIELD PLAN v2 — start from zero, both repos deleted

**Status:** AUTHORITATIVE — supersedes the "evolve Bid_it" decision in `ARCH_plan.md`.
**Decision (owner, 2026-07-25):** delete `fleet_fuel_system` AND `Bid_it`; build the
all-in-one financial workspace from an empty repository.
**What survives:** knowledge, not code. `BA_bidit.md` (the feature catalog, the 20+
correctness invariants, requirements M1–…), `BA_fleet_fuel.md` (the VAT/transport
specification R1–R76 with its case-law rules), the founder's charter, and the prompt
library. These documents are the specification the new system is built against.
**What does not survive:** every line of code, both test suites (761 + 2,422), all git
history, all migrations. The tests' *knowledge* is encoded in the requirements; the
tests themselves are rebuilt as the code is rebuilt.

---

## 0. Ground rules

1. **Zero code inheritance.** Nothing is copied from either old repo — not a file, not a
   snippet, not a fixture. (The old repos also contain real client PII; the quarantine
   rules in `PROMPTS.md` Part E apply to the archives forever.)
2. **Specification inheritance is total.** Every invariant the old systems proved the
   hard way is a day-one requirement here, not a retrofit: the new platform is *born*
   with tenancy isolation, append-only money, structural authorization, and audit
   chaining — the things the old code had to have bolted on.
3. **Scalability is a stated requirement at every layer** (owner directive). Every
   technology choice below carries its scaling path. We still build a modular monolith
   first — scalable ≠ microservices on day one; it means *nothing we ship blocks the
   next order of magnitude*.

---

## 1. The product (unchanged)

The charter stands: multi-tenant SaaS financial workspace — supplier-invoice intake →
processing → approval; employee expenses; customer invoicing (the AR engine of
`BA_bidit.md` §3 is the crown jewel spec); payments in/out; reconciliation; analytics;
reports/exports; org management; integrations; SaaS billing. Vertical differentiator:
the EU VAT-refund / transport module specified by R1–R76. Target segments and UX
benchmarks per the charter.

---

## 2. Coding languages & frameworks (explicit decisions)

### 2.1 Backend — **Python 3.12+, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2**

Chosen because it is the *proven* stack for this exact domain — the retired platform
demonstrated that this combination handles multi-tenant RLS, Decimal-safe money,
concurrency-safe numbering (`SELECT … FOR UPDATE`), EN-16931 XML, and PDF generation.
Rebuilding on it means every requirement in `BA_bidit.md` translates 1:1 with no
impedance mismatch, which matters enormously for a small team on a deadline.

Alternatives considered and rejected:

| Stack | Why not |
|---|---|
| **Go** (chi/echo + sqlc) | Best raw performance and deployment story, but slower feature velocity, a much thinner ecosystem for accounting artifacts (XML e-invoice, PDF, OCR, SEPA), and `float64` culture around money requires constant discipline. Our bottleneck is Postgres and I/O, not language CPU. |
| **TypeScript end-to-end** (NestJS/Fastify + Prisma) | One language across the stack is attractive, but JS `number` is a float — every money path needs decimal.js discipline; Prisma's RLS/session-variable story is weak; the financial-format ecosystem (camt.053, pain.001, Factur-X) is thinner. |
| **Java/Kotlin + Spring** | Enterprise-credible, but heavyweight for a one-engineer team; slowest iteration loop of the options. |
| **Elixir/Phoenix** | Excellent concurrency, but niche hiring pool and weak accounting-format ecosystem. |

Scaling path: FastAPI apps are **stateless** (12-factor; sessions in DB/Redis, files in
object storage) → horizontal scale is "run more replicas behind a load balancer".
Async I/O means one worker holds many in-flight requests; CPU-bound work (OCR, PDF,
XML) is *never* done in a request — it goes to the job queue (§3.4).

### 2.2 Frontend — **TypeScript, React 18+, Vite, Tailwind CSS, TanStack Query**

Industry-default SPA stack: largest hiring pool, best component ecosystem, and the UX
benchmark products (Stripe, Linear, Ramp) are built this way. Server state via TanStack
Query (cache, retries, optimistic updates); forms with zod validation mirroring the
backend's Pydantic schemas. Accessibility (WCAG 2.1 AA) is a definition-of-done item
from the first screen, not a later audit.

Scaling path: static assets on a CDN; the SPA talks to the API tier only; nothing about
the frontend limits horizontal scale.

### 2.3 Infrastructure languages

- **SQL (Postgres dialect)** is a first-class language of the system: RLS policies,
  constraints, partial unique indexes, and `SKIP LOCKED` queues are *application logic*
  and are code-reviewed as such.
- **Terraform** for infrastructure once there is more than one environment; **GitHub
  Actions YAML** for CI from day one.
- **Python** for tooling/scripts (one scripting language, not three).

---

## 3. Databases & data architecture (explicit decisions)

### 3.1 System of record — **PostgreSQL 16+** (one database, one truth)

Postgres is the only defensible choice for this product and the owner's scalability
requirement, because the platform's hardest guarantees are *database* guarantees:

- **Multi-tenancy:** every business row carries `org_id`; three isolation layers —
  (1) query-scoped filters, (2) an ORM-level automatic tenant guard, (3) **Row-Level
  Security** keyed to a per-transaction session variable — plus **composite foreign keys
  `(org_id, child_id) → parent(org_id, id)`** so a cross-tenant reference is impossible
  *in the schema*, not just in code. A CI job asserts RLS-policy coverage == the set of
  tenant-scoped tables (set equality, both directions).
- **Money:** `NUMERIC(14,2)` columns; Decimal end-to-end; ROUND_HALF_UP at the edges;
  no float ever touches a money path (CI greps for it).
- **Correctness under concurrency:** gap-free per-entity invoice numbering via
  `SELECT … FOR UPDATE` on the issuer row + `UNIQUE(org_id, number)` as the backstop;
  optimistic-locking `version` columns on workflow entities; unique constraints as the
  final word on idempotency (recurring periods, bank txn ids, email sends).
- **Append-only structures:** the audit trail (hash-chained, per-tenant monotonic
  sequence) and the settlement ledgers (corrections are offsetting negative entries)
  are tables with **no UPDATE/DELETE grants** for the app role.
- **JSONB** for frozen snapshots (issued-invoice seller/buyer), extraction payloads,
  and webhook envelopes — flexible without schema sprawl.

**Scaling path (staged, none of it blocks the previous stage):**

| Stage | Trigger | Move |
|---|---|---|
| S1 | day one | Single Postgres node, WAL archiving + PITR, pgbouncer in front (RLS-compatible: session variables set per-transaction, not per-connection) |
| S2 | read pressure | Read replicas for analytics/report queries (the projection layer reads replicas; the ledger never does) |
| S3 | large tenants / audit volume | **Declarative partitioning**: audit + event tables by time range; the biggest business tables hash-partitioned by `org_id` if needed. Archive partitions to cheap storage |
| S4 | true multi-node need | Citus (distributed by `org_id` — the schema is already keyed for it) **or** tenant sharding by org group across clusters. The `org_id`-everywhere discipline is exactly what makes both possible later |

### 3.2 Cache / coordination — **Redis 7+**

Shared-store **rate limiting** (the old system's per-process limiter multiplied by
replica count — a documented flaw we fix by design), short-TTL caches (FX rates,
permission matrices), idempotency-key reservations, and WebSocket/pub-sub fan-out if
live UI updates arrive. Redis is *never* a system of record: losing it costs
performance, not data. Scale: managed Redis with a replica; keys namespaced per org.

### 3.3 Files — **S3-compatible object storage** (content-addressed)

Invoice PDFs, receipts, attachments, generated reports: stored by SHA-256 under
`org/{org_id}/sha256/{hash}`, deduplicated, served via short-lived signed URLs after a
tenancy + permission check — never a public bucket, never a filesystem path in the DB.
Integrity sweeps re-hash against the recorded digest. Scale: object storage is
effectively infinitely horizontal; lifecycle rules move cold documents to archive
tiers while retention/legal-hold metadata lives in Postgres.

### 3.4 Jobs & queues — **Postgres-backed durable queue first** (`FOR UPDATE SKIP
LOCKED`), interface-isolated

Extraction/OCR, PDF/XML rendering, emails, webhooks, dunning, recurring generation,
report scheduling — all off the request path from day one. A Postgres queue gives
transactional enqueue (job + business row commit atomically), which brokers cannot;
it comfortably handles this product's volumes. The queue is behind a small interface
so stage-2 (dedicated worker pools per lane: cpu/io) and stage-3 (Redis Streams or
RabbitMQ if fan-out demands it) are swaps, not rewrites. Scheduler entries are
idempotent by `(kind, date, org)` key — the "recurring jobs must not duplicate
invoices" guarantee is a unique constraint, not a hope.

### 3.5 Search & analytics

Start with **Postgres**: FTS (tsvector) for document/invoice search; the Explore/pivot
engine as parameterized SQL over the record (aggregated in the database, never in app
memory). Escalate to **OpenSearch** (search) or **ClickHouse** (analytics) only when a
measured query profile demands it — the projection layer isolates reads so the swap is
contained. **No data warehouse before there is data volume.**

### 3.6 Observability store

OpenTelemetry traces + structured JSON logs + Prometheus metrics from the first
deploy; managed backends (Grafana Cloud tier or equivalent). SLOs: p95 API latency,
queue oldest-pending age, error rate — alarmed, not just graphed.

---

## 4. Scalability blueprint (layer by layer)

The owner's directive is "all must be scalable". Concretely:

| Layer | Day-one design | Scale move (no rewrite) |
|---|---|---|
| API tier | Stateless FastAPI containers | Add replicas behind LB; autoscale on p95 |
| Workers | Same image, `--role worker`, lanes cpu/io | Scale lanes independently |
| Postgres | One node + pgbouncer + PITR | Replicas → partitions → Citus/sharding (§3.1) |
| Redis | Single managed instance | Replica + cluster mode |
| Files | S3 content-addressed | Inherently horizontal; lifecycle tiers |
| Rate limits | Redis token bucket (global, per-org and per-token) | Already replica-proof |
| Tenancy | `org_id` + RLS everywhere | Enables partition/shard by tenant later; data-residency = a routing layer, schema unchanged |
| Frontend | CDN static + API-only | Nothing to do |
| Deploy | Docker Compose (one VM) | Kubernetes when >1 node is justified; images unchanged |
| CI | GitHub Actions: lint, typecheck (mypy strict + tsc), unit, Postgres job (RLS + concurrency + migrations), e2e, docker build | Parallel jobs; merge queue when team >1 |

Two honest anti-goals, stated so they are chosen and not stumbled into:
- **No microservices** until a bounded context demonstrably needs independent scaling
  or deployment cadence. The module boundaries (§5) are the future service seams.
- **No Kubernetes on day one.** One VM with Compose + PITR backups serves the first
  paying customers; the images and 12-factor discipline make the K8s move mechanical.

---

## 5. Architecture (carried over from ARCH_plan — it was designed for this)

Modular monolith, **8 bounded contexts + 2 projection layers** (the architect's
correction of the charter's 10-module framing stands: Dashboard and Reports are
projections over the record, never owners of math):

1. **Identity & Tenancy** (orgs, users, memberships, roles, SSO, sessions)
2. **The Record** (suppliers/customers/partners masters; documents; retention)
3. **AP** (capture → extract → review → approve → pay; the 14-state workflow spec)
4. **AR** (issuer entities, numbering, draft→issued lifecycle, credit notes,
   recurring, delivery — rebuild exactly to `BA_bidit.md` §3; it is the most
   valuable spec we own)
5. **Expenses** (state machine, policy engine, approvals, reimbursement)
6. **Banking & Cash** (statements, reconciliation-as-annotation, payment runs, SEPA,
   dunning)
7. **Platform** (audit chain, jobs, webhooks, notifications, billing/entitlements,
   file security)
8. **Transport vertical** (VAT refund R1–R76 + excise + overcharge — a plug-in
   context: own tables, module-gated, reads the record through services, adds no
   column to core tables)

Projections: **Analytics/Explore** and **Reports/Exports** — read-only, replica-friendly,
one dimension registry, one currency-conversion rule.

Layering enforced by a boundary test from the first week: `models → core → services →
api`; business logic never in route handlers; every module exposes an explicit
service interface. AI features follow the charter: suggestions with confidence scores,
reviewable, **never silently mutating a financial record** — the extraction-provider
seam is designed in from the start.

---

## 6. Security & compliance floor (day-one, non-negotiable)

The full invariant list lives in `PROMPTS.md` Part A and `BA_bidit.md` §7.1 — it is
unchanged and binding. Highlights that are *structural* here rather than retrofitted:
deny-by-default authorization as a **route dependency** with a CI test that every
mutating route declares a permission (both directions); maker≠checker on payment runs
and vendor bank-detail changes from their first implementation; org-status checked
per-request; opaque 404s; envelope-encrypted secrets (KEK seam → cloud KMS);
file-security choke point before any parse; production config validation that crashes
at boot; GDPR erasure + export designed into the record from the start.

---

## 7. Roadmap (greenfield, one engineer + AI, honest numbers)

Estimates are engineer-days. Greenfield removes the debt-repair work but re-adds
foundation and rebuild work; **time-to-chargeable is longer than the evolve path was —
this is the accepted cost of the decision.**

| Milestone | Theme | Content | Exit criteria | Effort |
|---|---|---|---|---|
| **M0** | Foundation | Repo, CI, deploy skeleton; tenancy spine (org_id + RLS + parity test + composite FKs); authz framework (structural); audit chain; money core; job queue; file gate; config validation | CI green incl. Postgres RLS job; a request cannot reach data without tenant + permission checks; invariant tests pass | **35–45d** |
| **M1** | AR engine | Rebuild `BA_bidit.md` §3 exactly: issuers, numbering (concurrency-proven), lifecycle, credit notes, snapshots, VAT engine, PDF + Factur-X, recurring, delivery, cash application | The §3 acceptance suite green incl. 16-worker numbering test on real Postgres; PDF == stored values | **45–60d** |
| **M2** | AP + documents | Upload/email intake, extraction seam (+ first OCR provider), review UI, approval workflow, storage/search/retention | An invoice goes upload→extract→review→approve→exportable record | **40–55d** |
| **M3** | Expenses + payments | Expense state machine + policy + reimbursement; payment runs + SEPA + maker≠checker; reconciliation annotation | Charter modules 3 & 5 demonstrable end-to-end | **35–50d** |
| **M4** | Sellable SaaS | Billing (Stripe), plans/entitlements, onboarding, dashboards + reports projections, scheduled exports; a11y pass | **First paying tenant possible** | **30–40d** |
| **M5** | Transport vertical | R1–R76 via Part E harvest prompt: claim engine, gates, workbook, excise, overcharge | The R-suite acceptance tests green; a claim pack builds with every legal gate enforced | **70–100d** |
| **M6** | Integrations & enterprise | Bank APIs (PSD2 aggregator), accounting exports (DATEV et al.), Peppol, SSO/SCIM completion, forecasts, NL reporting | Per-integration | **60–90d** |

Cumulative to first revenue (M0–M4): **185–250 days**. Full charter: **≈ 315–440 days**.
Sequence M1 before M2 because the AR spec is the sharpest document we own and it
exercises every foundation invariant early (numbering, snapshots, ledgers, PDF).

---

## 8. First 10 greenfield work orders (replace PROMPTS Part B for the new repo)

| # | Order | Delivers |
|---|---|---|
| G-1 | **New repo bootstrap** — see §9 ordering; scaffold backend/frontend/CI/compose; commit the spec docs into `docs/` first | A green empty build with the specs in-tree |
| G-2 | **Money + time core** — Decimal money lib (q2/ROUND_HALF_UP), date/period helpers; property-based tests | The no-float guarantee, tested |
| G-3 | **Tenancy spine** — orgs/users/memberships; org_id + RLS on the first tables; the set-equality CI test; composite-FK pattern; opaque-404 convention | Isolation proven before any business feature exists |
| G-4 | **Structural authz** — permission vocabulary, role matrix, route-dependency enforcement, both-direction CI coverage test | No unguarded route can merge |
| G-5 | **Audit chain + events** — append-only hash-chained audit, per-tenant sequence, offline verifier CLI | Every later mutation lands audited |
| G-6 | **Job queue + scheduler** — SKIP LOCKED queue, idempotent daily scheduler, worker entrypoint, SLO probe | Async foundation for OCR/email/PDF |
| G-7 | **File gate + object storage** — content-addressed S3 store, magic-byte validation, AV seam, signed-URL serving behind authz | Safe uploads before any intake feature |
| G-8 | **AR data model + numbering** — issuers, customers, issued invoices, the FOR-UPDATE numbering + unique backstop + 16-worker concurrency test | The crown jewel's skeleton, concurrency-proven |
| G-9 | **AR lifecycle + VAT engine** — draft→approve→issue, immutability, credit notes, server-side VAT, snapshots | Legal-grade invoicing |
| G-10 | **AR outputs** — PDF renderer + EN-16931 CII + Factur-X hybrid, idempotent email delivery, mark-viewed | PDF == stored values; sellable invoicing |

Each order is written up via the Part C template with Part A prepended (Part A's
codebase-facts appendix is regenerated for the new repo in G-1).

---

## 9. Bootstrap & double-decommission runbook (ORDER MATTERS)

The old Part F covered one repo; with both repos dying, the ordering below prevents
the spec from being orphaned. **Do not delete anything until step 4 is verified.**

1. **Create the new repository** (owner names the product; repo private).
2. **Commit the surviving knowledge into it first**: `docs/plan/` (this file,
   `BA_bidit.md`, `BA_fleet_fuel.md`, `ARCH_plan.md` marked superseded-but-referenced,
   `PROMPTS.md`), the charter, and the PII-quarantine rules. Push; verify on the host.
3. **Run WO-6 Step 1 (deny-list) while the old repos are still readable** — it needs
   them; afterwards only structural patterns remain possible.
4. **Archive both repos** per Part F.2 (git bundle + worktree zip + SHA-256 manifest,
   owner-held, offline, never on a git host). Two bundles, two manifests.
   - Fleet Fuel's archive additionally holds client business records under statutory
     VAT retention — the owner's continuity duty (live clients, 30-Sep deadline) is
     unchanged from Part F.1 and must be answered before deletion.
5. **Delete both repositories** on GitHub (owner; irreversible after ~90-day support
   window). Remove them from every automation, session source list, and local clone.
6. **Post-deletion verification** in the new repo: specs present and pushed; CI green
   on the G-1 scaffold; PII scan job required; `docs/plan/GREENFIELD_plan.md` records
   archive digests' existence (not their location) and deletion dates.

---

## 10. Risks specific to this decision

| Risk | Impact | Mitigation |
|---|---|---|
| Knowledge loss — 3,183 tests' worth of encoded behaviour discarded | Rebuilt features silently diverge from proven behaviour | The BA specs are the contract; every milestone's exit criteria reference them; Part D FinTech review prompt checks rebuilt math against the spec, not the reviewer's memory |
| Longer runway to revenue (~M4 vs the evolve path's shorter route) | Commercial pressure, morale | Stated and accepted at decision time (§7); M1 ships a demonstrable crown jewel early |
| Second-system effect — over-engineering the rebuild | Timeline blowout | §4's anti-goals; the Part C template's out-of-scope discipline; no microservices/K8s/warehouse before measured need |
| Solo-engineer bus factor | Delivery stalls | Everything spec'd in-repo; AI-executable prompt library; boring, hireable stack |
| Old-repo PII resurfacing via archives | GDPR exposure | Quarantine rules bind the archives; deny-list + structural CI scan in the new repo from G-1 |
| Live Fleet Fuel clients during the gap | Missed statutory deadlines | Owner continuity plan is a precondition of deletion (§9.4); the transport vertical (M5) is the destination |

---

*End of greenfield plan. On approval: execute §9 steps 1–2 (new repo + spec commit),
then G-1.*
