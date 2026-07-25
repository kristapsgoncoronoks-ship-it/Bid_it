# PROMPT LIBRARY — building the all-in-one financial workspace

> **⚠ DECISION UPDATE (2026-07-25 — see `GREENFIELD_plan.md`, which is authoritative):**
> BOTH repositories (`fleet_fuel_system` AND `Bid_it`) will be deleted; the product is
> rebuilt **from zero** in a new repository. Consequences for this library:
> **Parts C, D and E remain fully valid** (template, reviews, VAT harvest — they are
> code-independent). **Part A's repo-facts appendix and all of Part B (WO-1…WO-10)
> target the Bid_it codebase and are SUPERSEDED** — the greenfield work orders are
> `GREENFIELD_plan.md` §8 (G-1…G-10); regenerate Part A's appendix against the new repo
> in G-1. **Part F now runs TWICE** (once per repo) with the ordering rules of
> `GREENFIELD_plan.md` §9 — the new repo receives the specs BEFORE anything is deleted.

**Author:** Senior Prompt Engineer
**Date:** 2026-07-24 (greenfield banner added 2026-07-25)
**Derived from:** `ARCH_plan.md` (the architecture plan — authoritative), `BA_bidit.md`, `BA_fleet_fuel.md` (R1–R76).
**Audience:** an AI engineering agent with filesystem + shell access to `/home/user/Bid_it`.

Every path, symbol and command in this library was verified against the repository on 2026-07-24.
If a prompt names a symbol that no longer exists, **stop and report** — do not invent a replacement.

---

## How to use this library

1. **Always prepend Part A (Master Context Prompt)** to any work prompt. It is the system prompt.
2. Then paste **exactly one** work order from Part B (or one generated from the Part C template).
3. When the work order is finished, run the relevant Part D specialist review prompt(s) as a **separate session** with fresh eyes. A review session may not fix code; it reports.
4. Part E is the transport/VAT harvest prompt. It is prepended (in addition to Part A) to every work order in Epic G.
5. Part F is the one-time Fleet Fuel decommission runbook — the `fleet_fuel_system` repository is being **deleted**; run Part F once (after WO-6 Step 1), then never reference that repo again.

Chaining rule: **one concern per session, one concern per PR.** WO-1 through WO-10 execute in order; only WO-4, WO-5, WO-6, WO-7, WO-8 and WO-10 may run in parallel with each other (see each order's `Depends`).

---

## Table of contents

| § | Prompt | Purpose |
|---|---|---|
| **A** | [Master Context Prompt](#part-a--master-context-prompt) | Reusable system prompt for ANY work on this codebase |
| **B1** | [WO-1 — Structural authorization](#wo-1--structural-authorization-router-dependency--ci-coverage) | A1.1→A1.2→A1.3, 13–18d, P0 |
| **B2** | [WO-2 — Vendor bank-detail control](#wo-2--vendor-bank-detail-control) | A2.1+A2.2+A2.3, 7–9d, P0 |
| **B3** | [WO-3 — Partners router lockdown](#wo-3--partners-router-lockdown) | A3.1, 1–2d, P0 |
| **B4** | [WO-4 — Tenant & session integrity](#wo-4--tenant--session-integrity) | B1.1+B1.2+B1.4, 3–5d, P0 |
| **B5** | [WO-5 — Mandatory inbound-email secret](#wo-5--mandatory-inbound-email-secret) | B1.6, 1–2d, P0 |
| **B6** | [WO-6 — Fleet Fuel PII quarantine + harvest protocol](#wo-6--fleet-fuel-pii-quarantine--harvest-protocol) | G0.1+G0.2, 2–4d, P0 |
| **B7** | [WO-7 — One validation engine](#wo-7--one-validation-engine) | C1.1, 3–5d, P0 |
| **B8** | [WO-8 — One FX convention](#wo-8--one-fx-convention-no-silent-cross-currency-sums-scheduled-refresh) | C1.2+C1.3+C1.4, 7–10d, P0 |
| **B9** | [WO-9 — Payment-run controls](#wo-9--payment-run-controls) | D1.1+D1.2+D1.3, 7–9d, P0 |
| **B10** | [WO-10 — Docs truth-up, ADRs, M0 exit gate](#wo-10--documentation-truth-up-the-new-adrs-and-the-m0-exit-gate) | J1.1+J1.2+B1.3, 5–7d, P0 |
| **C** | [Work-Order Template](#part-c--work-order-template) | Generate every future work order from the roadmap |
| **D** | [Specialist Review Prompts](#part-d--specialist-review-prompts) | Security · QA · DB · Performance · FinTech · UX |
| **E** | [VAT / Transport Harvest Prompt](#part-e--vattransport-harvest-prompt) | Port Fleet Fuel R1–R76 as specification |
| **F** | [Fleet Fuel Decommission](#part-f--fleet-fuel-decommission-run-once) | One-time runbook: archive → delete the fleet_fuel_system repo → verify |

---
---

# PART A — MASTER CONTEXT PROMPT

> **Copy everything between the markers. Prepend to every work prompt.**

<!-- ═══════════════ COPY FROM HERE: MASTER CONTEXT ═══════════════ -->

You are a senior backend/full-stack engineer working on **InvoiceIQ**, the codebase at `/home/user/Bid_it`. Read this entire brief before touching a file. It overrides your defaults.

## 1. The product and who pays for it

InvoiceIQ is a **multi-tenant SaaS financial workspace for SMEs and accountancy practices**: supplier-invoice capture and approval (AP), customer invoicing (AR), payments and settlement, expenses, analytics, exports, and organization/identity administration. A **transport vertical** (EU cross-border VAT refunds under Directive 2008/9/EC, fuel/toll line-item analytics, diesel excise) is being added as a plug-in bounded context, harvested as *specification* from a retired system called Fleet Fuel.

Who pays: finance leads at 2–50-person SMEs, and accountancy practices managing many client workspaces. They pay because the product (a) stops them re-keying invoices, (b) recovers cash they would otherwise forfeit, and (c) produces an audit-ready financial record. **Every one of those promises dies if a number is wrong or a tenant sees another tenant's data.**

Team reality: **one engineer plus AI assistance.** Work must be reviewable, incremental and shippable. Never propose a rewrite.

## 2. Stack and repo layout (verified)

```
/home/user/Bid_it
├── Makefile                      # the command surface — use it
├── .github/workflows/ci.yml      # 5 jobs: lint · backend · postgres · frontend · release
├── .pre-commit-config.yaml       # ruff lint + ruff format on ^backend/
├── docs/
│   ├── architecture/adr/0001..0022*.md   # 22 ADRs — THE specification. Keep true.
│   ├── architecture/{overview,data-model,domain-modules,engineering-rules,
│   │                 security-boundaries,data-flows,foundation,deployment}.md
│   ├── product/{product-requirements,personas,pricing-hypothesis,metrics,risks,workflows}.md
│   ├── security/{authorization-policy-matrix,cross-tenant-isolation-report,...}.md
│   └── DECISIONS-NEEDED.md, BACKLOG.md
├── backend/                      # FastAPI + async SQLAlchemy 2.0 + Alembic
│   ├── app/
│   │   ├── main.py               # app factory, exception handlers, middleware
│   │   ├── worker.py, seed.py, openapi.py
│   │   ├── models/    (46 modules)   ORM tables + enums. Bottom layer.
│   │   ├── core/      (17 modules)   authz, config, database, tenant, money, storage,
│   │   │                             errors, security, keyvault, ratelimit, residency,
│   │   │                             dimensions, metrics, observability, security_headers, roles
│   │   ├── services/  (75 modules)   ALL business logic
│   │   ├── schemas/   (43 modules)   Pydantic request/response models
│   │   └── api/
│   │       ├── deps.py, router.py
│   │       └── routes/ (38 modules)  thin controllers only
│   ├── alembic/versions/         # 61 revisions, SINGLE head
│   └── tests/                    # 115 modules, ~737 test functions (~761 collected)
└── frontend/                     # React 18 + Vite + TypeScript + Tailwind
    ├── src/pages/ (39 pages)  src/components/{ui,shell}/  src/lib/{api,types,format}.ts
    ├── src/auth/  src/design/ (a fixture-driven IA showcase, NOT wired to the live app)
    └── e2e/{smoke,visual}.spec.ts   # Playwright
```

**ARCHIVAL WARNING:** `README.md` and `ARCHITECTURE.md` at the repo root are **materially stale** — they describe a ~12-test analytics MVP. Do not trust them and do not cite them. `docs/architecture/adr/*` and `docs/product/*` are the real specification.

## 3. Layering — machine-enforced

```
models  →  core  →  services  →  api
```

- `models` imports nothing from `app.services` or `app.api`.
- `core` is infrastructure; imports nothing from `app.services` or `app.api`.
- `services` hold **all** business logic; must **not** import `app.api`. A service signals failure with `app.core.errors.AppError`, never `fastapi.HTTPException`.
- `api/routes/*` are **thin controllers**: parse → call a service → shape a response. Business logic in a route is a defect (`docs/architecture/engineering-rules.md` §3).

Enforced by `backend/tests/test_boundaries.py` (AST import inspection):
`test_models_do_not_import_services_or_api`, `test_core_does_not_import_services_or_api`, `test_services_do_not_import_the_web_layer`, `test_app_package_is_importable`.

## 4. NON-NEGOTIABLE INVARIANTS

Violating any of these is a release blocker, not a review comment.

**Tenancy**
1. **Three-layer tenant isolation**: per-query `org_id` filters + the ORM `do_orm_execute` guard over the tenant-model registry + Postgres `FORCE ROW LEVEL SECURITY`. All three, always.
2. **RLS/model set-equality**: `tests/test_rls.py::test_rls_migration_covers_every_tenant_table` asserts the union of `TENANT_TABLES` across migrations **equals** the tenant-model set **exactly**. A new tenant-scoped table ships its RLS policy **in the same migration** or CI fails.
3. **Composite `(org_id, id)` foreign keys** for cross-table references inside a tenant.
4. **Opaque 404, never 403**, on a cross-tenant fetch by id. Object-id guessing must yield zero information.
5. The Postgres app role stays **`NOSUPERUSER`** — a superuser bypasses RLS entirely.

**Authorization**
6. **Deny-by-default.** `app/core/authz.py` is the single source of truth: `Permission` (20 members), `Role` (8 business roles), `ROLE_PERMISSIONS`. A role grants exactly its listed row. Routes ask for a *permission*, never inspect a raw role.
7. After WO-1, authorization is **structural**: declared on the router (`APIRouter(dependencies=[...])`) with per-route overrides for stricter verbs, and CI asserts total coverage in **both directions**.
8. **Segregation of duties** where money moves: an AP submitter cannot approve their own invoice; an expense claimant cannot approve their own report; a payment-run maker cannot be its checker; a vendor bank-detail requester cannot approve their own request.

**Money**
9. **`Decimal`, ROUND_HALF_UP, never float.** Use `app/core/money.py::q2` / `q`. Storage is `Numeric(14,2)`. `tests/test_money_invariants.py::test_money_never_uses_float` scans for float paths.
10. **The server recomputes every total.** A client-supplied total is ignored, never trusted.
11. **Payment status is derived, never stored.** `SUM(ledger) == cached amount_paid` must hold on both AR and AP ledgers.
12. **Append-only settlement ledgers.** A reversal is a *negative entry*, never a delete or an update.
13. **No overpayment / no over-crediting**, enforced under a row lock (`SELECT … FOR UPDATE`).
14. **No aggregate sums across currencies** without a recorded conversion. If it cannot convert, it reports per-currency or refuses — it never labels a foreign amount EUR.
15. **One FX convention**: ECB reference rates are *units per 1 EUR*; converting to EUR **divides**. `fx_source ∈ {eur, stated, ecb, unknown}`; `unknown` yields `NULL`, never a guessed number.

**Audit & evidence**
16. **Every mutating operation is audited** via `app/services/audit.py::record(db, action, *, target_type, target_id, meta, org_id, actor)`. It is append-only and **hash-chained** with a per-tenant monotonic `seq`. It never raises; it adds to the session and the caller's commit persists it atomically with the operation.
17. **Never redact or rewrite an audit row** — it would break the hash chain. GDPR erasure pseudonymises the subject and retains the chain.
18. **An issued document is immutable.** Correction is by credit note, whose effect is derived.

**AI**
19. **AI never silently mutates a financial record.** Any AI seam is opt-in, default-off, advisory, strict (never invents a field), best-effort (falls back to the deterministic chain), independently verified against the source document, and gated by a DLP classifier that persists `{type, count}` findings and **never the matched value**. With all AI settings at defaults the system must run end to end with **zero external calls**.

**Wire contract (frozen)**
20. Every error response is `{"detail": "<message>", "code": "<stable slug>"}` plus an `X-Request-ID` header. Every route declares a `response_model`. The SPA and the test suite both depend on this shape. **Move logic between layers freely; never move it across the wire.**

## 5. Commands (verified — run from the repo root unless stated)

```bash
make install      # backend venv + frontend deps (one-time)
make test         # cd backend && . .venv/bin/activate && python -m pytest -q
make lint         # ruff check app tests && ruff format --check app tests
make fmt          # ruff check --fix app tests && ruff format app tests
make typecheck    # mypy app/core        <-- NOTE: narrower than CI
make check        # lint + typecheck + test
make migrate      # alembic upgrade head
make migration m="add vendor_change_requests"   # autogenerate a revision
make openapi      # regenerate backend/openapi.json
make build        # frontend: tsc --noEmit && vite build
make up / down / logs                            # docker compose
```

**CI is authoritative and is stricter than `make typecheck`.** Reproduce CI exactly before declaring done:

```bash
cd backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests
mypy app                                   # WHOLE app — this is what CI runs
test "$(alembic heads | wc -l)" -eq 1      # single head
alembic upgrade head && alembic check      # applies cleanly, no model drift
python -m pytest -q                        # full suite on SQLite
cd ../frontend && npm run build            # tsc --noEmit + vite build
npx playwright test e2e/smoke.spec.ts      # when the change touches the SPA
```

Postgres-only gate (RLS + concurrency) — run it whenever you touch tenancy, migrations, locks or numbering:

```bash
# a local Postgres with a NOSUPERUSER role, matching .github/workflows/ci.yml
export RLS_TEST_DATABASE_URL=postgresql+asyncpg://appuser:apppw@localhost:5432/invoiceiq
cd backend && python -m pytest tests/test_rls.py tests/test_numbering_concurrency.py -q
```

**Baseline discipline:** before you change anything, run `python -m pytest -q` and **record the exact pass count** in your notes. That number is the regression net. Report it before and after.

## 6. Coding conventions

- Python 3.11. `from __future__ import annotations` at the top of every module. Full type annotations — CI runs `mypy app`.
- Async everywhere in the request path: `AsyncSession`, `await db.scalar(...)`, `await db.scalars(...)`, `select()` 2.0 style. No sync DB calls.
- Ruff formats; do not hand-format. Line length and style come from `backend/pyproject.toml`.
- Naming: services expose verbs (`vendors.update_vendor`), routes expose HTTP handlers, schemas end in `In`/`Out`/`Create`/`Update`/`Detail`.
- Docstrings state **why**, especially the fail-open vs fail-closed decision at every gate. That reasoning is the single most valuable non-obvious asset in this codebase — never delete it, and add it when you write a new gate.
- Migrations: `alembic revision --autogenerate`, then **read and edit the generated file**. Add the RLS policy for any new tenant table. Never edit a migration that has shipped.
- Frontend: TypeScript strict, TanStack Query for server state, Tailwind for styling, existing primitives in `src/components/ui/` (`DataTable`, `EmptyState`, `ErrorState`, `QueryState`, `Skeleton`, `ConfirmDialog`, `Form`, `Modal`, `PageHeader`, `StatusBadge`, …). Do not introduce a new UI library.
- The frontend's permission-aware rendering is **cosmetic only**. The server is the control. Never treat a hidden nav item as a security boundary.

## 7. Definition of done

A unit of work is done only when **all** of these are true:

1. **Error handling** — every failure path produces `{"detail", "code"}`; services raise `AppError`; nothing is swallowed. A bare `except: pass` is a defect.
2. **Loading, empty and error states** on every new screen (use `QueryState`/`EmptyState`/`ErrorState`).
3. **Validation** at the schema boundary *and* the invariant enforced in the service (schemas catch shape; services catch business rules).
4. **Audit logging** on every mutation, with actor and old→new where a value changed.
5. **Permissions** declared structurally on the route and proven by a test for at least one granted role and one denied role.
6. **Tests** — unit for pure logic, service/integration for the real query path, authorization tests, and a financial-correctness assertion where money is involved. Negative and adversarial cases are mandatory (see §8).
7. **Documentation** — the ADR or `docs/` page that describes this area is updated in the same PR. If the change contradicts a doc, fix the doc.
8. **CI green**, reproduced locally per §5, with the baseline pass count unchanged or explained line-by-line.

## 8. Adversarial cases you must cover (not optional)

Every work order that touches data or money adds tests for the applicable subset:

- **Cross-tenant**: tenant B's id passed to tenant A's session → **404, never 403**; a list endpoint bound to A returns **zero** of B's rows even when the data overlaps (same invoice numbers, same names, same amounts).
- **Permission-denied**: the lowest role that should be refused actually gets 403; the lowest role that should be allowed actually gets 2xx.
- **Over-credit**: crediting more than the invoice total is refused (1-cent tolerance, `credited_total` clamped).
- **Over-payment**: allocating more than the receipt's unallocated balance or the invoice's outstanding is refused under a row lock.
- **Replayed idempotency key**: the second call is a no-op returning the first result, not a duplicate row.
- **Concurrency**: two simultaneous writers to the same aggregate — exactly one wins, and the loser's state is *unchanged* (not partially applied).
- **Stale version**: an optimistic-concurrency write with an old `version` returns **409**.
- **Mixed currency**: an aggregate spanning currencies either converts with a recorded rate or refuses. It never sums raw.
- **Malformed input at the boundary**: an invalid IBAN, an out-of-range tax rate, a negative quantity, a date before the epoch — each refused with 422 and a stable `code`.

## 9. When you are blocked, or the plan looks wrong

- **If a symbol or path named in the work order does not exist:** stop, report what you found instead, and propose the correction. Never invent a replacement name and proceed.
- **If the work order contradicts an invariant in §4:** the invariant wins. Stop and report the contradiction.
- **If a change requires a product/legal/commercial decision** (a plan ladder, a chart of accounts, whether reconciling a bank line settles an invoice): stop. Write the options and the recommendation into `docs/DECISIONS-NEEDED.md` and report. Do not decide it yourself in code.
- **If the work order is larger than it looked:** deliver the smallest coherent shippable slice, make it green, and report exactly what is left with an estimate. **Never leave the tree in a state that does not compile, migrate and test clean.**
- **If a test breaks:** first determine whether the test was asserting correct behaviour. If it was, your change is wrong. If it was asserting a security gap (e.g. an endpoint that should never have been open), **raise the fixture's privilege — never weaken the assertion** — and record the change in your report.

## 10. Prohibitions

- **No placeholder code.** No `TODO`, no `pass  # implement later`, no `raise NotImplementedError` in a path a user can reach. If you cannot finish it, do not merge it.
- **No invented functionality.** Build what the work order specifies. If you think something else is needed, report it as a proposal.
- **No skipped or weakened tests.** No `@pytest.mark.skip`, no `xfail`, no loosened tolerance, no deleted assertion — unless the work order explicitly instructs it and you state why in your report.
- **No unaudited financial mutation.** Any write that touches an amount, a status that governs an amount, a bank identifier, a fee or a ledger emits an audit event in the same transaction.
- **No float in a money path.** Ever.
- **No business logic in a route module.**
- **No new tenant-scoped table without an RLS policy in the same migration.**
- **No claim of production-readiness without evidence.** "Done" means you pasted the commands you ran and their output. Never write "should work", "production-ready" or "fully tested" without the command output that proves it.
- **No copying code, constants, fixtures or data from the retired Fleet Fuel system.** Its repository (`kristapsgoncoronoks-ship-it/fleet_fuel_system`) is scheduled for **deletion** (Part F) and contained real client PII. Its rules survive as specification in `docs/plan/BA_fleet_fuel.md` (R1–R76). If a clone, bundle or archive of it is ever encountered, rules may be read; bytes may not be copied.
- **No secrets, real IBANs, real VAT numbers or real company names** in code, tests or fixtures. Fixtures are synthetic — realistic in *shape*, fictional in *content*.

## 11. Reporting format

End every session with:

```
## What changed
<files touched, one line each, grouped by layer>

## Commands run (with output)
<paste the actual command lines and their tail>

## Baseline
Before: N passed
After:  M passed  (delta explained: +X new tests, Y fixtures raised in privilege, 0 assertions weakened)

## Invariants preserved
<name each §4 invariant this touched and how it is proven>

## Left undone / follow-ups
<explicit, with an estimate — or "none">
```

<!-- ═══════════════ END: MASTER CONTEXT ═══════════════ -->

---
---

# PART B — THE FIRST TEN WORK ORDERS

Execute in order. Each assumes Part A is prepended. Each is self-contained enough to run in a fresh session.

**Dependency summary**

| WO | Board ids | Effort | Depends on | May run in parallel with |
|---|---|---|---|---|
| WO-1 | A1.1→A1.2→A1.3 | 13–18d | — | WO-4, WO-5, WO-6, WO-7, WO-8, WO-10 |
| WO-2 | A2.1+A2.2+A2.3 | 7–9d | WO-1 | WO-3 |
| WO-3 | A3.1 | 1–2d | WO-1 | WO-2 |
| WO-4 | B1.1+B1.2+B1.4 | 3–5d | — | anything |
| WO-5 | B1.6 | 1–2d | — | anything |
| WO-6 | G0.1+G0.2 | 2–4d | — | anything |
| WO-7 | C1.1 | 3–5d | — | anything |
| WO-8 | C1.2+C1.3+C1.4 | 7–10d | — | anything |
| WO-9 | D1.1+D1.2+D1.3 | 7–9d | WO-1, WO-2 | — |
| WO-10 | J1.1+J1.2+B1.3 | 5–7d | all of M0 for the exit gate | — |

**Parallel non-engineering ask, raise in week 1 (H1.1):** Stripe live credentials + per-plan Price IDs + Billing Meter `event_name`, plus the EU VAT seller-of-record decision; a dev IdP (Okta/Entra/Keycloak) for SAML; a real chart of accounts for DATEV/SAF-T. These have legal/finance lead times of weeks and sit on the critical path to revenue. Track them in `docs/DECISIONS-NEEDED.md`.

---
## WO-1 — Structural authorization: router dependency + CI coverage

<!-- ═══════════════ COPY FROM HERE: WO-1 ═══════════════ -->

**WORK ORDER 1 — Structural authorization (board A1.1 → A1.2 → A1.3). Effort 13–18 days. Priority P0. Depends on: nothing. Everything else in M0 depends on this.**

### Objective and business value

Authorization today is an **imperative call inside each handler** — `authz.require(current, authz.Permission.X)` written by hand, route by route. Coverage is therefore a per-route discipline with **no structural guarantee**, and the discipline has already failed in the highest-consequence place in the system: `POST /vendors` and `PATCH /vendors/{id}` carry no permission check at all, and they set the `iban` that `app/services/sepa.py::payment_run_sepa` pays.

Fixing those routes by hand fixes today's instance. This order fixes **the class**: authorization becomes a declared property of a router that CI can enumerate, so an unclassified route cannot ship. Commercially this is what lets you put a stranger's money data in the system at all — it is the entry gate to every paying tenant.

### Scope

**In scope**
- A dependency factory in `app/core/authz.py` usable as `APIRouter(dependencies=[Depends(require_perm(Permission.X))])`.
- Converting **all 38 modules** under `backend/app/api/routes/` to declared permissions.
- Newly gating the confirmed-open endpoints.
- `backend/tests/test_authz_coverage.py`, asserted in both directions.
- Role fixtures in `backend/tests/conftest.py` so tests can act as a lower-privilege user.
- `docs/architecture/adr/0024-structural-authorization.md`.

**Out of scope**
- Making the four currently-unreachable business roles storable (that is A1.5 / a later order). `authz.business_role()` already resolves them forward-compatibly; do not change the role model here.
- Audit coverage enforcement (A1.4 / a later order).
- Any change to `ROLE_PERMISSIONS` content. **The matrix is correct; only its enforcement is being changed.** If a route seems to need a permission that does not exist, stop and report — do not add a `Permission` member.

### Files to touch

| File | Change |
|---|---|
| `backend/app/core/authz.py` | add `require_perm(*permissions)` factory + `PUBLIC_ROUTES` allow-list constant |
| `backend/app/api/deps.py` | no signature change; the factory depends on `CurrentUser` |
| `backend/app/api/routes/*.py` (all 38) | declare router-level and per-route permissions |
| `backend/tests/conftest.py` | add `role_client` factory fixture (see below) |
| `backend/tests/test_authz_coverage.py` | **new** |
| `docs/architecture/adr/0024-structural-authorization.md` | **new** |
| `docs/security/authorization-policy-matrix.md` | append the enforcement section |

### Implementation guidance

**Step 1 — the factory.** In `app/core/authz.py`, add:

```python
def require_perm(*permissions: Permission):
    """FastAPI dependency: enforce EVERY given permission on the current user.
    Used as APIRouter(dependencies=[Depends(require_perm(Permission.X))]).
    Carries `.permissions` so tests/CI can introspect what a route declares."""
```

The returned dependency must depend on `app.api.deps.CurrentUser` and call the existing `authz.require`. **Do not duplicate the permission-resolution logic** — `permissions_for()` stays the single resolver. Attach the declared permission tuple to the dependency callable (e.g. `dep.__ffs_permissions__ = permissions`) so the coverage test can read it off `route.dependencies` without executing anything.

`app/core/authz.py` must not import `app.api` (boundary test `test_core_does_not_import_services_or_api`). Resolve this by having the factory take the user via a *locally imported* dependency inside the function body, or by placing the factory in `app/api/deps.py` and re-exporting the permission introspection helper from `core/authz.py`. **Pick one, state why in the ADR, and make `tests/test_boundaries.py` pass — do not weaken the boundary test.**

**Step 2 — sweep the 38 routers.** For each module in `app/api/routes/`:
1. Determine the router's **read** permission (e.g. `invoices.py` → `INVOICE_READ`, `issued.py` → `ISSUED_READ`, `payment_runs.py` → `PAYMENT_READ`, `analytics.py` → `REPORT_READ`, `audit.py` → `AUDIT_READ`, `team.py` → `MEMBER_READ`, `billing.py` → `BILLING_MANAGE`).
2. Declare it at `APIRouter(prefix=..., tags=..., dependencies=[Depends(require_perm(...))])`.
3. Declare **stricter** permissions per route on writes/approvals/sends (`INVOICE_WRITE`, `INVOICE_APPROVE`, `ISSUED_WRITE`, `ISSUED_SEND`, `PAYMENT_WRITE`, `EXPORT_RUN`, `MEMBER_MANAGE`, `ROLE_ASSIGN`, `SETTINGS_MANAGE`, `BILLING_MANAGE`).
4. Remove an in-handler `authz.require` **only** where the declared dependency is equal or stricter. Where in doubt, keep both. **Never relax.**
5. Keep `modules.require_enabled(db, org_id, key)` calls exactly where they are — a module entitlement is an *additional* gate, never a substitute for a permission.

**Step 3 — newly gate these confirmed-open endpoints** (they are the security-gap inventory the architect verified): the six KPI endpoints in `analytics.py`, `GET /team/members`, `GET /webhooks`, `GET /jobs`, `GET /access/*`, `GET /modules`, `GET /settings/validation`.

**Step 4 — `PUBLIC_ROUTES`.** A small, explicitly-reviewed allow-list of `(method, path)` pairs that legitimately carry no permission:
- auth bootstrap: register, login, password-reset request/confirm, email verification, invitation accept, SSO callback endpoints;
- `/health`, `/health/ready`, `/health/queue`, `/metrics` if exposed;
- webhook receivers that carry their own signature authentication (`POST /email/inbound`, the Stripe/EveryPay webhook, SCIM which authenticates by bearer token of its own);
- any public share endpoint.

Each entry carries a one-line reason **in the same structure**, not in a comment. An entry with no reason fails the test.

**Step 5 — `tests/test_authz_coverage.py`.** Enumerate `app.main.app.routes`. For each `APIRoute`:
- collect declared permissions from the route's own `dependencies` **and** from its owning router's dependencies;
- if none, the `(method, path)` must be in `PUBLIC_ROUTES` **with a reason**, else fail with the route named;
- assert **the reverse**: every `PUBLIC_ROUTES` entry resolves to a live route (this closes the Fleet Fuel defect where `share_revoke` was classified in two structures but did not exist);
- prove the test works: build a throwaway `APIRouter` with an unclassified route inside the test, mount it on a scratch `FastAPI()` instance, and assert the checker reports it. Do **not** mount a fixture route on the real app.

**Step 6 — test fixtures.** `backend/tests/conftest.py` currently offers only `auth_client` (a freshly-registered **owner**). Add a factory fixture that yields a client authenticated as a chosen stored role, following the pattern already used in `tests/test_authz.py:139`:

```python
@pytest_asyncio.fixture
async def role_client(client, db_session):
    """Return an async factory: await role_client("user_free") -> AsyncClient
    authenticated as a member of a fresh org whose stored role is that value.
    Stored roles today are UserRole.{user_free,user,admin,owner}; they resolve to
    business roles READ_ONLY/EMPLOYEE/ADMINISTRATOR/OWNER via authz.business_role."""
```

Do not change `UserRole` and do not add role values.

### Invariants this order must preserve

- Deny-by-default (§4.6). Nothing becomes *more* permissive. If converting a route would widen access, keep the in-handler check and note it.
- `ROLE_PERMISSIONS` content unchanged; `GET /api/v1/auth/authz-matrix` and `docs/security/authorization-policy-matrix.md` stay in lock-step (`tests/test_authz.py::test_every_role_is_in_the_matrix` must remain green **unmodified**).
- The layering rule (§3). `tests/test_boundaries.py` stays green unmodified.
- The wire contract (§4.20): a denial is still `403` with `{"detail","code"}`. Do **not** change a cross-tenant 404 into a 403 as a side effect.

### Database / migration impact

**None.** This order adds no table, no column and no migration. If you find yourself writing one, you have left the scope.

### Testing requirements

Add or extend:
1. `tests/test_authz_coverage.py::test_every_route_declares_a_permission_or_is_public` — the forward direction.
2. `tests/test_authz_coverage.py::test_public_routes_allowlist_has_no_stale_entries` — the reverse direction.
3. `tests/test_authz_coverage.py::test_coverage_checker_detects_an_unclassified_route` — the self-test on a scratch app.
4. `tests/test_authz_coverage.py::test_every_public_route_entry_states_a_reason`.
5. `tests/test_authz.py::test_require_perm_dependency_denies_missing_permission` — a route under a router dependency returns 403 for a role lacking it, **with no in-handler call**.
6. `tests/test_authz.py::test_route_level_override_is_stricter_than_router_default` — a per-route stricter declaration wins.
7. For each newly-gated endpoint from Step 3, one denied-role case and one granted-role case, e.g. `tests/test_authz_routes.py::test_analytics_kpis_require_report_read`, `::test_jobs_list_requires_settings_manage`, `::test_modules_list_requires_settings_manage`.
8. Cross-tenant regression: `tests/test_cross_tenant_isolation.py` and `tests/test_isolation.py` must stay green **unmodified** — adding a permission must not turn an opaque 404 into a 403.

### EXPECTED TEST BREAKAGE — read this before you start

Converting the routers **will break an unknown but significant number of the ~761 baseline tests**, because many use `auth_client` (an owner) but some use lower-privilege fixtures against endpoints that were previously open. **This breakage is the deliverable, not an accident: it is the inventory of the security gap.**

Triage protocol, in this order:
1. **Was the endpoint legitimately open?** → add it to `PUBLIC_ROUTES` with a reason, and say so in the report.
2. **Was the test using a role that genuinely should have access?** → the permission you declared is wrong. Fix the declaration, not the test.
3. **Was the test using a role that should NOT have access?** → **raise the fixture's role** to the lowest role that legitimately has the permission. Record `test_file::test_name — raised from <old role> to <new role>` in the report.
4. **Never** lower an assertion, loosen a status-code check, add `skip`/`xfail`, or delete a test to go green.

The final report must contain the complete list from step 3. That list is the most valuable artifact this work order produces — it is the security-gap inventory for the whole platform.

Budget: A1.2 alone is 8–12 days. Do not compress it.

### Acceptance criteria (verifiable checklist)

- [ ] `require_perm()` exists, is introspectable, and reuses `authz.permissions_for` (no duplicated resolution logic).
- [ ] All 38 modules in `app/api/routes/` declare a router-level permission or are fully covered by per-route declarations.
- [ ] No mutating route (`POST`/`PATCH`/`PUT`/`DELETE`) relies on an in-handler check alone.
- [ ] The six analytics KPI endpoints, `GET /team/members`, `GET /webhooks`, `GET /jobs`, `GET /access/*`, `GET /modules`, `GET /settings/validation` each return **403** for `user_free` and **2xx** for a role that holds the permission.
- [ ] Adding an unclassified route fails CI (proved by the self-test).
- [ ] Removing a route named in `PUBLIC_ROUTES` fails CI.
- [ ] `tests/test_authz.py::test_every_role_is_in_the_matrix` passes **with the file unmodified**.
- [ ] `tests/test_boundaries.py` passes **with the file unmodified**.
- [ ] Full suite green; the pass count equals baseline + the new tests; the PR body lists every fixture privilege raise.
- [ ] `mypy app` clean; `ruff check` + `ruff format --check` clean.
- [ ] ADR-0024 written and referenced from `docs/security/authorization-policy-matrix.md`.

### Rollback strategy

The change is additive at the router level and behaviour-preserving at the service level, so rollback is a revert of the PR — no data migration, no state to unwind. If a production issue appears after deploy and a revert is not immediately possible, the *narrow* mitigation is to move the offending `(method, path)` into `PUBLIC_ROUTES` with a reason of `"INCIDENT-<id>: temporarily de-gated, re-gate by <date>"` — which keeps CI honest, keeps the route enumerated, and leaves an audit trail of the exception. Never delete the coverage test to unblock a deploy.

### Documentation to update

- `docs/architecture/adr/0024-structural-authorization.md` — the decision, the `PUBLIC_ROUTES` policy, the both-directions assertion, and the "raise the fixture, never lower the assertion" rule.
- `docs/security/authorization-policy-matrix.md` — a new "How it is enforced" section pointing at the factory and the coverage test.
- `docs/architecture/engineering-rules.md` — one line: a new route declares a permission or is added to `PUBLIC_ROUTES` with a reason.

### Self-verification block — run before declaring done

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests
mypy app
python -m pytest tests/test_authz.py tests/test_authz_coverage.py tests/test_boundaries.py -q
python -m pytest tests/test_cross_tenant_isolation.py tests/test_isolation.py -q
python -m pytest -q                       # full suite — compare to the recorded baseline
python - <<'PY'
# every route is classified — the same check CI runs, printed for the report
from app.main import app
from fastapi.routing import APIRoute
from app.core.authz import PUBLIC_ROUTES  # adjust import to where you placed it
missing = []
for r in app.routes:
    if not isinstance(r, APIRoute):
        continue
    perms = getattr(r, "_declared_permissions", None)  # your introspection hook
    if not perms and not any((m, r.path) in PUBLIC_ROUTES for m in r.methods):
        missing.append(sorted(r.methods), )
print("UNCLASSIFIED:", missing or "none")
PY
cd ../frontend && npm run build
```

Then answer, in the report: how many tests broke, how many were fixture privilege raises, how many were wrong declarations, how many endpoints went onto the allow-list and why.

<!-- ═══════════════ END: WO-1 ═══════════════ -->

---

## WO-2 — Vendor bank-detail control

<!-- ═══════════════ COPY FROM HERE: WO-2 ═══════════════ -->

**WORK ORDER 2 — Vendor bank-detail control, the payment-redirection fraud vector (board A2.1 + A2.2 + A2.3). Effort 7–9 days. Priority P0. Depends on: WO-1.**

### Objective and business value

Verified in `backend/app/api/routes/vendors.py` today: `POST /vendors` and `PATCH /vendors/{vendor_id}` have **no permission check, no `audit.record`, no version guard and no IBAN validation** — and they write `Vendor.iban` / `Vendor.bic`, which `app/services/sepa.py::build_pain001` (line 48) turns into a creditor account in a real bank file. **Any authenticated member of any tenant can redirect a supplier payment today.** This is risk S-1 in the plan, scored 9/9.

Business value: this is the single control an auditor, an insurer and a prospect's security review will all ask about. Without it you cannot responsibly onboard a paying tenant.

### Scope

**In scope**
- A new `app/services/vendors.py`; the route becomes thin.
- Permission + audit + optimistic `version` on vendor create/update.
- A new `app/core/bank_id.py` — IBAN ISO 13616 structure + **ISO 7064 MOD-97** check digits, BIC format — applied at every write path and **inside `sepa.build_pain001`**.
- The Fleet Fuel **hard fraud-safety invariant** (BA §3.B / R23) as `vendor_change_requests`.
- A frontend pending-changes approval screen.

**Out of scope**
- Vendor dedup beyond the existing exact stripped-name match (that is A2.4, M6).
- Live VAT-number validation against VIES (never inline; a later advisory check).
- Employee/issuer IBAN *change-request* workflow — this order only adds **format validation** to those write paths, not the second-approver flow.

### Files to touch

| File | Change |
|---|---|
| `backend/app/core/bank_id.py` | **new** — pure functions, no DB, no HTTP |
| `backend/app/services/vendors.py` | **new** — all vendor business logic |
| `backend/app/api/routes/vendors.py` | thin controller; `get_or_create_vendor` moves to the service |
| `backend/app/models/vendor.py` | add `version`, `status` (`active` \| `provisional`) |
| `backend/app/models/vendor_change_request.py` | **new** |
| `backend/app/schemas/vendor.py` | add change-request schemas; keep `VendorOut` shape backward-compatible |
| `backend/app/services/sepa.py` | validate every creditor IBAN/BIC before emitting |
| `backend/app/services/payment_run.py` | refuse a run containing a vendor with a pending IBAN change |
| `backend/alembic/versions/<new>.py` | tables + columns + **RLS policy for `vendor_change_requests`** |
| `frontend/src/pages/Vendors.tsx` | pending-changes list + approve/reject |
| `backend/tests/test_vendors_authz.py`, `test_bank_id.py`, `test_vendor_change_requests.py` | **new** |

### Implementation guidance

**Step 1 — `app/core/bank_id.py`.** Pure, dependency-free:

```python
def normalize_iban(value: str) -> str      # strip spaces, upper-case
def is_valid_iban(value: str) -> bool      # length by country (ISO 13616) + MOD-97 == 1
def is_valid_bic(value: str) -> bool       # 8 or 11 chars, AAAABBCC[DDD], letters/digits per position
def assert_iban(value: str) -> str         # returns normalized, raises AppError(code="invalid_iban")
```

MOD-97: move the first four characters to the end, map letters `A→10 … Z→35`, interpret as an integer, `% 97 == 1`. Country lengths must come from a table in this module — at minimum every SEPA country. An **unknown country prefix is rejected**, not waved through.

**Step 2 — the service.** Move `get_or_create_vendor`, create and update into `app/services/vendors.py`. The service raises `AppError`; the route maps it. Every mutation calls `audit.record(db, "vendor.create"|"vendor.update", target_type="vendor", target_id=vendor.id, meta={...})` with `meta` carrying **old→new for each changed field** — and, for `iban`, only the **last 4 characters** of each value plus the full length, never the whole IBAN in the audit meta.

**Step 3 — optimistic concurrency.** Add `version: Mapped[int]` default 1. `PATCH` requires the client's `version`; a mismatch is **409** with code `stale_version`. Mirror the pattern already used on `Invoice.version` (see `app/api/routes/invoice_review.py::_bump`).

**Step 4 — the fraud-safety invariant (the core of this order).**

Three fields are **protected**: `iban`, `tax_id` (VAT number) and any company registration number field.

- On an **existing** vendor, a request that changes a protected field **must not write it**. It creates a row in `vendor_change_requests` (`pending → approved | rejected`) recording `field`, `old_value`, `new_value`, `requested_by`, `source_document_id` (nullable FK to the vaulted document the value was read from), `requested_at`.
- The route returns **202** (or 200 with the unchanged vendor plus a `pending_changes` block) — but **never** the new value as if it were stored. Choose one and document it in the response model; do not leave it ambiguous.
- Approval requires a **different user** holding `SETTINGS_MANAGE`. `requested_by == approver_id` → **403**, code `maker_is_checker`.
- Approval applies the value, bumps `version`, and audits `vendor.change_approved` with old→new. Rejection audits `vendor.change_rejected` with the reason.
- A **brand-new** vendor may be created carrying captured `iban`/`tax_id`, but lands with `status="provisional"`.
- **A payment run refuses to include a vendor with a pending protected-field change**, and refuses to include a `provisional` vendor unless explicitly confirmed. The refusal names the vendor.

Non-protected fields (`name`, `country`, `category`, address/contact) update normally, audited.

**Step 5 — SEPA.** In `sepa.build_pain001`, validate every creditor IBAN (and BIC when present) **before** any XML is produced. An invalid one raises — the file is never produced with a bad account. Do this *in addition to* write-time validation; write-time validation can be bypassed by a data migration or a direct DB edit, and the file is the last line of defence.

**Step 6 — frontend.** In `frontend/src/pages/Vendors.tsx`: a "Pending bank-detail changes" section listing requester, field, old→new (IBAN masked except last 4), source-document link, and Approve/Reject. Approve is hidden for the requester and, because the frontend is cosmetic only, **the server still enforces it**. Use `ConfirmDialog` for approve. Include loading, empty and error states.

### Invariants this order must preserve

- §4.6/4.7 deny-by-default and structural authorization (built in WO-1 — use `require_perm`, do not hand-roll).
- §4.8 segregation of duties: requester ≠ approver.
- §4.16 audit on every mutation, in the same transaction.
- §4.4 opaque 404 on a cross-tenant vendor id — do not let the new service leak existence via a different status code.
- §4.2 the new `vendor_change_requests` table is tenant-scoped and **must ship its RLS policy in the same migration**, and appear in the tenant-model registry so `test_rls.py::test_rls_migration_covers_every_tenant_table` stays green.

### Database / migration impact

One migration adding:
- `vendors.version INTEGER NOT NULL DEFAULT 1`
- `vendors.status VARCHAR NOT NULL DEFAULT 'active'`
- table `vendor_change_requests` with `org_id`, composite FK `(org_id, vendor_id) → vendors(org_id, id)`, `field`, `old_value`, `new_value`, `status`, `requested_by`, `requested_at`, `decided_by`, `decided_at`, `decision_note`, `source_document_id`
- index on `(org_id, vendor_id, status)`
- **RLS policy** matching the existing tenant-table pattern in prior migrations
- a partial/filtered unique constraint or an explicit service check preventing **two open pending requests for the same `(vendor, field)`**

Backfill: existing vendors get `version=1`, `status='active'`. Existing rows with a **structurally invalid IBAN** must **not** be silently cleared — flag them by creating a report (a log line + a row count in the migration's output) and leave the data alone. Deleting a customer's data in a migration is never acceptable.

Verify: `alembic upgrade head && alembic check` clean; `test "$(alembic heads | wc -l)" -eq 1`.

### Testing requirements

`backend/tests/test_bank_id.py`
- `test_valid_ibans_per_country` — table-driven over ≥10 SEPA countries with correct lengths and check digits (synthetic values only).
- `test_invalid_check_digits_rejected` — a structurally correct IBAN with a mutated check digit fails.
- `test_unknown_country_prefix_rejected`.
- `test_iban_length_mismatch_rejected` — right country, wrong length.
- `test_bic_format_8_and_11_accepted_others_rejected`.

`backend/tests/test_vendors_authz.py`
- `test_invalid_iban_rejected` — `POST /vendors` with `iban="DE00000000000000000000"` returns **422** with `code="invalid_iban"` and no row is created.
- `test_employee_role_cannot_create_vendor` — `role_client("user")` → 403.
- `test_employee_role_cannot_update_vendor` → 403.
- `test_read_only_role_can_list_vendors` → 200.
- `test_vendor_create_is_audited` — an `AuditEvent` with action `vendor.create` exists, actor = the caller.
- `test_stale_version_returns_409`.
- `test_cross_tenant_vendor_patch_returns_404_not_403`.

`backend/tests/test_vendor_change_requests.py`
- `test_iban_change_on_existing_vendor_does_not_mutate_the_row` — stored IBAN unchanged; exactly one pending request exists.
- `test_tax_id_change_creates_pending_request`.
- `test_requester_cannot_approve_own_change` → 403, `code="maker_is_checker"`.
- `test_second_approver_applies_change_and_audits` — value applied, `version` bumped, `vendor.change_approved` audited with old→new.
- `test_rejected_change_leaves_value_and_audits`.
- `test_payment_run_refuses_vendor_with_pending_iban_change` — creating/paying a run that includes that vendor is refused and **names the vendor**.
- `test_new_vendor_with_captured_iban_is_provisional`.
- `test_duplicate_open_request_for_same_field_is_refused`.
- `test_sepa_build_refuses_invalid_creditor_iban` — construct a run whose vendor IBAN was invalidated directly in the DB; `build_pain001` raises and **no XML is returned**.
- `test_audit_meta_never_contains_a_full_iban` — scan the audit `meta` JSON for the stored IBAN string; assert absent.

Existing suites that must stay green unmodified: `tests/test_sepa.py`, `tests/test_payment_runs.py`, `tests/test_reimbursement_sepa.py`, `tests/test_rls.py`.

### Acceptance criteria (verifiable checklist)

- [ ] An `EMPLOYEE`-equivalent member gets 403 on vendor create and update; a permitted role gets 2xx.
- [ ] Every vendor change produces an audit event naming old→new; no full IBAN appears in audit meta.
- [ ] A structurally invalid IBAN is refused at write **and** cannot reach a payment file.
- [ ] Changing an existing vendor's IBAN leaves the stored IBAN unchanged and creates exactly one pending request.
- [ ] The requester cannot approve their own request (403, `maker_is_checker`).
- [ ] A payment run against a vendor with a pending IBAN change is refused, naming the vendor.
- [ ] A stale `version` returns 409.
- [ ] `vendor_change_requests` has an RLS policy and is in the tenant-model registry; `test_rls.py` green.
- [ ] Cross-tenant vendor access still returns 404, never 403.
- [ ] Full suite green; `mypy app` clean; migration applies cleanly with a single head.

### Rollback strategy

Two-stage. The **code** is revertible by PR revert. The **migration** is additive (new table, new nullable-with-default columns) so a downgrade is safe and must be written and *tested* (`alembic downgrade -1` then `upgrade head`). If pending change requests exist at rollback time, the downgrade must not silently apply or drop them: write the downgrade to **refuse** if any row has `status='pending'`, forcing an explicit operator decision. Document that refusal in the migration docstring.

### Documentation to update

- `docs/architecture/data-model.md` — the new table and the protected-field rule.
- `docs/security/authorization-policy-matrix.md` — vendor bank-detail approval requires `SETTINGS_MANAGE` **and** a different user.
- A short note in `docs/architecture/adr/0024-structural-authorization.md` or a new fragment recording that protected-field changes are a *workflow*, not a write.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic upgrade head && alembic check
alembic downgrade -1 && alembic upgrade head        # downgrade is real, not decorative
python -m pytest tests/test_bank_id.py tests/test_vendors_authz.py tests/test_vendor_change_requests.py -q
python -m pytest tests/test_sepa.py tests/test_payment_runs.py tests/test_reimbursement_sepa.py -q
python -m pytest -q
# RLS parity on real Postgres
RLS_TEST_DATABASE_URL=postgresql+asyncpg://appuser:apppw@localhost:5432/invoiceiq \
  python -m pytest tests/test_rls.py -q
cd ../frontend && npm run build
```

Confirm in the report: paste the assertion output proving the stored IBAN was unchanged after an attempted update.

<!-- ═══════════════ END: WO-2 ═══════════════ -->

---

## WO-3 — Partners router lockdown

<!-- ═══════════════ COPY FROM HERE: WO-3 ═══════════════ -->

**WORK ORDER 3 — Partners router lockdown (board A3.1). Effort 1–2 days. Priority P0. Depends on: WO-1.**

### Objective and business value

`backend/app/api/routes/partners.py::_guard` is, verbatim:

```python
async def _guard(db: DbSession, org_id: str):
    await modules.require_enabled(db, org_id, "issuing")
```

That is a **module entitlement check, not an authorization check**. Any member of an issuing-enabled org can create partners and — worse — **sign the contract/acceptance documents that gate whether an invoice may be issued at all** (`_enforce_partner_gate`). A signed document here is a commercial assertion; it must be attributable and permissioned.

### Scope

**In scope:** permissions on every verb in `partners.py`; `audit.record` on create, update and **document sign**; keep the module gate as an additional check.

**Out of scope:** changing partner readiness logic, the penalty calculation, or `_enforce_partner_gate`'s 409 behaviour. This order changes **who may call**, not what happens.

### Files to touch

`backend/app/api/routes/partners.py`, `backend/app/services/partners.py`, `backend/tests/test_partners.py` (extend), `backend/tests/test_partners_authz.py` (**new**).

### Implementation guidance

1. Declare `ISSUED_READ` at the router via the WO-1 `require_perm` dependency.
2. Declare `ISSUED_WRITE` per route on `POST /partners`, `PATCH /partners/{id}`, document upload, and **document sign**. (Prefer reusing `ISSUED_WRITE` over adding a `PARTNER_MANAGE` member — adding a `Permission` member forces a change to all 8 rows of `ROLE_PERMISSIONS` and to the published matrix. If you believe a dedicated permission is right, **stop and report** the proposal rather than deciding it here.)
3. Keep `_guard`'s `modules.require_enabled(db, org_id, "issuing")` call on every route. Entitlement and permission are orthogonal.
4. Add audit events in the **service**, not the route: `partner.create`, `partner.update`, `partner.document_upload`, `partner.document_sign` — the sign event recording actor, `partner_id`, document kind, and the signature timestamp.
5. If any partner logic currently lives in the route module, move it to `app/services/partners.py` while you are here — but only what you must touch; do not refactor the whole module.

### Invariants this order must preserve

- §4.6/4.7 deny-by-default, structural declaration.
- §4.16 audit on mutation; the sign event is the compliance artifact that justifies issuing.
- The existing partner gate behaviour: issuing to an unready partner still fails with **409** and the message naming the missing document (assert this explicitly so the lockdown cannot mask a regression).
- §4.4 cross-tenant partner id → 404.

### Database / migration impact

None.

### Testing requirements

`backend/tests/test_partners_authz.py`
- `test_employee_cannot_create_partner` → 403.
- `test_employee_cannot_sign_partner_document` → 403.
- `test_read_only_can_list_partners` → 200.
- `test_read_only_cannot_update_partner` → 403.
- `test_partner_routes_still_require_issuing_module` — with the module disabled, a fully-permissioned user still gets **403 from the module gate** (proves the two gates are independent and both live).
- `test_document_sign_is_audited` — `partner.document_sign` exists with actor, partner id and document kind.
- `test_cross_tenant_partner_returns_404`.

Extend `backend/tests/test_partners.py` with `test_issue_still_refused_for_unsigned_contract_409` asserting the exact existing message shape.

### Acceptance criteria

- [ ] Every verb in `partners.py` declares a permission; none relies on the module gate alone.
- [ ] An employee-equivalent role cannot create a partner or sign a document.
- [ ] Every sign is audited with actor, partner and document kind.
- [ ] The pre-existing 409 partner gate on issuing is unchanged and asserted.
- [ ] `test_authz_coverage.py` (from WO-1) passes with `partners.py` fully classified.
- [ ] Full suite green; `mypy app` clean.

### Rollback strategy

Pure PR revert; no schema, no data.

### Documentation to update

`docs/security/authorization-policy-matrix.md` — partner document signing requires `ISSUED_WRITE`.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/test_partners.py tests/test_partners_authz.py tests/test_authz_coverage.py -q
python -m pytest -q
```

<!-- ═══════════════ END: WO-3 ═══════════════ -->

---

## WO-4 — Tenant & session integrity

<!-- ═══════════════ COPY FROM HERE: WO-4 ═══════════════ -->

**WORK ORDER 4 — Tenant & session integrity (board B1.1 + B1.2 + B1.4). Effort 3–5 days. Priority P0. Depends on: nothing. May run in parallel with WO-1.**

### Objective and business value

`backend/app/api/deps.py::get_current_user` validates (a) the bearer token, (b) `user.is_active`, (c) a live session `jti` via `sessions.active`, and (d) an **active membership** via `memberships.get`. It **never checks `Organization.status`** — the org row is fetched only when `settings.enforce_region_pinning` is on. So **suspending a tenant does nothing for up to the token's 24h TTL**, and a role change does not invalidate a live token.

Business value: suspension is the lever you pull for non-payment, abuse or a security incident. A lever that takes 24 hours to act is not a lever.

### Scope

**In scope**
- Enforce `Organization.status == 'active'` on every request.
- Revoke live sessions on: org suspension, user deactivation, role change, tenant reassignment.
- Make the Postgres CI job a required PR check and confirm the app role stays `NOSUPERUSER`.

**Out of scope**
- Finishing the `users.org_id` → memberships dual-write (B1.5, a later order).
- Refresh-token rotation (J1.10).
- Tenant offboarding (B1.8).

### Files to touch

`backend/app/api/deps.py`, `backend/app/services/sessions.py`, `backend/app/services/team.py`, `backend/app/api/routes/platform.py`, `.github/workflows/ci.yml`, `backend/tests/test_membership_enforcement.py` (extend), `backend/tests/test_org_suspension.py` (**new**).

### Implementation guidance

1. In `get_current_user`, fetch the `Organization` **unconditionally** (it is already fetched under `enforce_region_pinning`; make the fetch shared so residency does not double-fetch). If `org is None` or `org.status != "active"`, raise **401** with a stable machine code such as `organization_suspended` — using the `{"detail","code"}` envelope (§4.20). Choose 401 over 403 deliberately and state why in the docstring: the credential is no longer usable, and a 401 makes the SPA log the user out rather than showing a confusing in-app error.
   - **Do not** add a second round-trip per request. Reuse the fetched org for `residency.assert_region(org)` and for `get_current_org`.
   - Platform-operator routes that run deliberately unscoped (`get_current_user_unscoped`) must keep working against a suspended tenant — that is how an operator un-suspends it. Assert this.
2. Session revocation. `app/services/sessions.py` already revokes for logout / sign-out-everywhere / password reset / deactivation — reuse that function; do not write a second mechanism. Call it from:
   - org suspension in `app/api/routes/platform.py` (revoke **all** sessions of **all** members of that org),
   - user deactivation and role change in `app/services/team.py`,
   - tenant reassignment wherever it lives.
   Each revocation is audited (`session.revoked_bulk` with the trigger and the count).
3. CI: confirm `.github/workflows/ci.yml`'s `postgres` job triggers on `pull_request` (it currently triggers on `push: [main]`, `pull_request` and `workflow_dispatch` at the workflow level — **verify it is not filtered at the job level**), and that the role creation line keeps `NOSUPERUSER`. If the repository's branch protection is not visible to you, write the required-check list into `docs/DEPLOYMENT.md` and report that it must be enabled in GitHub settings — do not claim it is enforced.

### Invariants this order must preserve

- §4.1–4.5 tenancy. Do not change the tenant ContextVar lifecycle; assert it still resets at both ends of a request so nothing leaks between requests.
- §4.20 error envelope; a new code is added, the shape is not changed.
- The existing 401 semantics for an invalid token must not change — a suspended-org 401 is distinguishable **only** by `code`, never by leaking whether the org exists.

### Database / migration impact

None expected. `Organization.status` already exists (`app/models/organization.py`). If you find a missing index on a session lookup you add during revocation, add it in a migration and say so.

### Testing requirements

`backend/tests/test_org_suspension.py`
- `test_suspended_org_rejects_next_request` — authenticate, suspend the org directly in the DB, assert the very next API call is 401 with `code="organization_suspended"`.
- `test_suspended_org_401_is_indistinguishable_from_invalid_token_except_by_code`.
- `test_platform_operator_can_still_act_on_a_suspended_org` — the unscoped operator path works, so suspension is reversible.
- `test_suspension_revokes_all_member_sessions` — two members logged in; suspend; **both** tokens dead.
- `test_reactivating_an_org_requires_new_login` (old tokens stay dead — revocation is not undone).

Extend `backend/tests/test_sessions.py` / `test_membership_enforcement.py`
- `test_role_change_revokes_sessions` — the old token is dead immediately after a role change.
- `test_user_deactivation_revokes_sessions`.
- `test_revocation_is_audited_with_trigger_and_count`.

Performance guard (cheap but real): `test_request_does_not_add_a_second_org_query` — count queries with a SQLAlchemy event listener across one authenticated request before/after the change; the delta must be ≤ 1.

### Acceptance criteria

- [ ] Suspending an org kills the **next** request from every member, not eventually.
- [ ] A role change forces re-auth.
- [ ] Platform-operator routes still function against a suspended tenant.
- [ ] Exactly one org fetch per request (no N+1 introduced).
- [ ] `.github/workflows/ci.yml` postgres job runs on `pull_request`; the app role is `NOSUPERUSER`; the required-check list is documented.
- [ ] `tests/test_membership_enforcement.py` green (extended, not weakened).
- [ ] Full suite green; `mypy app` clean.

### Rollback strategy

PR revert. Note the **one-way effect**: revoked sessions stay revoked after a rollback — users must log in again. State this in the deploy note; it is acceptable and safe, but it must not surprise anyone.

### Documentation to update

- `docs/architecture/security-boundaries.md` — org status is a per-request gate.
- `docs/DEPLOYMENT.md` — the required-check list for branch protection.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/test_org_suspension.py tests/test_sessions.py tests/test_membership_enforcement.py -q
python -m pytest tests/test_tenancy.py tests/test_isolation.py tests/test_cross_tenant_isolation.py -q
python -m pytest -q
RLS_TEST_DATABASE_URL=postgresql+asyncpg://appuser:apppw@localhost:5432/invoiceiq \
  python -m pytest tests/test_rls.py tests/test_numbering_concurrency.py -q
```

<!-- ═══════════════ END: WO-4 ═══════════════ -->

---

## WO-5 — Mandatory inbound-email secret

<!-- ═══════════════ COPY FROM HERE: WO-5 ═══════════════ -->

**WORK ORDER 5 — Mandatory inbound-email secret (board B1.6). Effort 1–2 days. Priority P0. Depends on: nothing.**

### Objective and business value

`backend/app/core/config.py:83` declares `inbound_email_secret: str | None = Field(default=None)` and `backend/app/api/routes/email.py:46` reads `expected = settings.inbound_email_secret` — checking it **only if it is set**. With it unset, **anyone who guesses a 16-hex (64-bit) inbound address token can inject documents into a tenant's review inbox**. That is a document-injection and social-engineering vector against the AP approval flow (risk S-5).

### Scope

**In scope:** make the secret mandatory in production via the existing boot-time validator; hard-401 the endpoint when absent or mismatched; keep development zero-config.

**Out of scope:** building a provider adapter (E1.6) and inbound-token rotation (B1.7). Do not start either.

### Files to touch

`backend/app/core/config.py`, `backend/app/api/routes/email.py`, `backend/tests/test_email_intake.py` (extend), `backend/tests/test_config_guards_authz.py` (extend), `.env.hostinger.example`, `docs/DEPLOYMENT.md`.

### Implementation guidance

1. In `Settings._validate_production` (`app/core/config.py:264`) — which already collects `problems` for a dev `secret_key`, a SQLite `database_url`, `kek_provider=env` without `kek_key`, and `*` CORS — append:
   ```python
   if self.email_intake_enabled_in_any_form and not self.inbound_email_secret:
       problems.append("inbound_email_secret is unset (set INBOUND_EMAIL_SECRET)")
   ```
   If there is no such feature flag, make it unconditional: production boot fails without the secret. **Simpler and stricter is correct here.** State the choice in the docstring.
2. In `app/api/routes/email.py`, replace the conditional check with an unconditional one: no configured secret **or** a mismatched/absent supplied secret → **401** with a stable `code` (e.g. `inbound_auth_failed`), and **compare in constant time** (`hmac.compare_digest`) so the check does not leak length or prefix.
3. **Preserve the correct existing design**: the tenant is resolved from the **recipient address token, never the sender**. The sender is trivially forgeable and forwarding breaks SPF/DKIM. Add a test that locks this in so nobody "improves" it later.
4. Update `.env.hostinger.example` and the deployment doc with the variable and how to generate it (`python -c "import secrets;print(secrets.token_urlsafe(32))"`).

### Invariants this order must preserve

- §4.20 error envelope; a 401 here must not reveal whether the recipient token maps to a real tenant. **Return the same 401 for a bad secret and an unknown recipient** — enumeration safety.
- Development and test environments (`environment != "production"`) keep booting with zero configuration.

### Database / migration impact

None.

### Testing requirements

`backend/tests/test_config_guards_authz.py`
- `test_production_boot_fails_without_inbound_email_secret` — constructing `Settings(environment="production", ...)` without it raises `ValueError` naming `inbound_email_secret`.
- `test_development_boot_unaffected`.

`backend/tests/test_email_intake.py`
- `test_inbound_rejects_missing_secret` → 401, `code="inbound_auth_failed"`.
- `test_inbound_rejects_wrong_secret` → 401, and **no** document, job or inbound row is created (assert counts, not just the status).
- `test_inbound_accepts_correct_secret` → the attachment lands in the review queue exactly as before.
- `test_unknown_recipient_token_returns_the_same_401_as_a_bad_secret` — enumeration safety.
- `test_tenant_is_resolved_from_recipient_not_sender` — craft a payload whose `From` belongs to tenant B and whose recipient token belongs to tenant A; the document lands in **A**.
- `test_secret_comparison_is_constant_time` — assert the code path calls `hmac.compare_digest` (a source/behaviour assertion is acceptable here; a timing test is not).

### Acceptance criteria

- [ ] Production boot raises without the secret, with a message naming the env var.
- [ ] The endpoint 401s when the secret is absent, wrong, or unconfigured.
- [ ] A rejected request creates **zero** rows and **zero** jobs.
- [ ] Unknown-recipient and bad-secret responses are identical.
- [ ] Tenant resolution from the recipient token is asserted by a test.
- [ ] Dev/test unaffected; full suite green; `mypy app` clean.

### Rollback strategy

PR revert. **Deployment ordering matters:** the secret must be present in the production environment *before* this ships, or the app will refuse to boot. Ship the env var first, then the code. Put that ordering in the deploy note.

### Documentation to update

`docs/DEPLOYMENT.md` (required env var + generation command), `.env.hostinger.example`, and the email-intake section of `docs/architecture/data-flows.md` if it describes the secret as optional.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/test_email_intake.py tests/test_config_guards_authz.py -q
python -m pytest -q
grep -rn "inbound_email_secret" app/ .env* ../docs/DEPLOYMENT.md
```

<!-- ═══════════════ END: WO-5 ═══════════════ -->

---
## WO-6 — Fleet Fuel PII quarantine + harvest protocol

<!-- ═══════════════ COPY FROM HERE: WO-6 ═══════════════ -->

**WORK ORDER 6 — Fleet Fuel PII quarantine + harvest protocol (board G0.1 + G0.2). Effort 2–4 days. Priority P0. Depends on: nothing — but its Step 1 needs a readable Fleet Fuel copy, so run WO-6 BEFORE executing the Part F repo deletion. This must also land BEFORE any transport (Epic G) work begins.**

### Objective and business value

The Fleet Fuel system (repository scheduled for deletion — Part F) is being harvested as a **specification** for the transport vertical; its rules live on in `docs/plan/BA_fleet_fuel.md`. It contains **real client personal and commercial data as module constants** — verified examples: `customer_master.CUSTOMERS` carries `«Client-EE» AS / EE1########0 / «street», «postcode» «city», Estonia` and `UAB «Client-LT-1» / LT1##########7`; there are also `BANKS`, `SUPPLIER_ACCOUNTS`, `supplier_master.SUPPLIERS` / `VAT_REGS` / `INVOICE_REG`, and `vat_config.INVOICES` / `ISSUERS`. Three databases (`customers.db`, `fuel_history.db`, `suppliers.db`) are **committed to git**.

If any of that crosses into `/home/user/Bid_it` — even into a test fixture, even once, even in a commit that is later reverted — it is a GDPR exposure in a repository that will be shared, mirrored and backed up. Git history is forever. This order makes the crossing **structurally impossible** and writes down how harvesting is allowed to work.

**This is a legal control, not a nice-to-have. It is risk L-3.**

### Scope

**In scope**
- A deny-list of every real identifier found in Fleet Fuel.
- `scripts/pii_scan.py` scanning the Bid_it **working tree and git history**.
- A CI job wiring the scan into every PR.
- Synthetic fixture generators for transport tests.
- `docs/transport/harvest-protocol.md` and an empty-but-structured `docs/transport/rules.md`.

**Out of scope**
- Any transport model, service or route. **Write no feature code in this order.**
- Deciding the fate of the Fleet Fuel repository itself — that is a legal decision. Raise it, do not act on it.

### Files to touch (all in `/home/user/Bid_it`)

`scripts/pii_scan.py` (**new**), `scripts/pii_denylist.txt` or `.json` (**new**), `.github/workflows/ci.yml` (new job), `backend/tests/factories/transport.py` (**new** — synthetic generators), `backend/tests/test_pii_scan.py` (**new**), `docs/transport/harvest-protocol.md` (**new**), `docs/transport/rules.md` (**new**).

### Implementation guidance

**Step 1 — build the deny-list (read-only on Fleet Fuel). ⚠ DELETION ORDERING: this step needs a readable copy of Fleet Fuel, so it MUST run before (or from the archive bundle of) the Part F decommission — after the repo is deleted and no bundle is reachable, skip to the structural patterns below and note the gap in `docs/transport/harvest-protocol.md`.**
Read the Fleet Fuel copy for identifiers only: company names, VAT numbers, registration numbers, addresses, IBANs/bank references, invoice-number literals, contact names, e-mail addresses, phone numbers. Start with `customer_master.py`, `supplier_master.py`, `vat_config.py`, `month_config.py`, and the committed `.db` files.

**The deny-list file is itself sensitive.** Do not commit the raw values in plaintext. Store **salted SHA-256 hashes** of each normalised token (lower-cased, whitespace-stripped) plus a non-identifying label and the token length, e.g.:

```json
{"label": "customer-1-name", "len": 12, "sha256": "…", "kind": "company_name"}
```

The scanner hashes candidate tokens from the repo the same way and compares. That gives you a CI gate that can fail loudly **without republishing the PII into the very repo you are protecting**. Document the salt's storage (an env var / CI secret, never committed) in the protocol.

Additionally include **structural** patterns that need no deny-list at all and catch anything you missed:
- EU VAT ids: `\b(EE|LT|LV|PL|CZ|DE|BE|FR|IT|ES|SE|DK|FI|NL|AT|SI|HU|HR|SK|RO|BG|PT|IE|LU|GR|EL)\d{8,12}\b`
- IBANs: `\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b` **that pass MOD-97** (a random hex string will not, so this has a low false-positive rate)
- and an allow-list of known-synthetic values used by existing fixtures, so the scan does not flag the demo tenant.

**Step 2 — `scripts/pii_scan.py`.** A dependency-free Python script with two modes:
- `--tree` — walk the working tree (skipping `.git`, `node_modules`, `.venv`, `dist`, `*.png`, caches).
- `--history` — for each deny-list entry, run `git log -S<token> --oneline --all`; because tokens are hashed, this mode must instead scan `git rev-list --all` blobs (`git grep` over all refs) — implement it as `git grep -I -n -e <pattern> $(git rev-list --all)` for the structural patterns, and for hashed literals scan every blob's normalised tokens. **History scanning is slow — cache by commit SHA and make the full history pass a scheduled/nightly job while the tree pass runs on every PR.**

Exit non-zero with the file, line and the **label** (never the value) on any hit.

**Step 3 — CI.** Add a `pii-scan` job to `.github/workflows/ci.yml`: runs on `pull_request`, executes `python scripts/pii_scan.py --tree`, and is a required check. Add a nightly (`schedule:`) run with `--history`.

**Step 4 — synthetic fixtures.** `backend/tests/factories/transport.py` generating data that is **realistic in shape, fictional in content**:
- VAT ids with a correct country prefix and length but a fictional numeric body, and a comment stating they are synthetic;
- IBANs generated with **valid MOD-97 check digits** so `core/bank_id` accepts them, over a documented test-only bank code;
- company names from an obviously fictional set (`"Northwind Haulage OÜ"`, `"Baltic Freight Demo UAB"`) — never a real carrier;
- invoice numbers matching each supplier's known *format* without reusing a real number;
- fuel line items with plausible litres/prices.

Every generator has a docstring saying: *synthetic, generated, never derived from client data.*

**Step 5 — `docs/transport/harvest-protocol.md`.** The binding rules for every future Epic-G PR:

1. **Read Fleet Fuel for rules; never copy its code.** The stacks differ (Flask + SQLite + procedural vs FastAPI + async SQLAlchemy + Postgres). A copied line is both a licence-of-origin question and a design regression.
2. **Never copy configuration, constants, fixtures or database files.**
3. Every harvested rule arrives as **three artifacts**: (a) a typed model or a pure function; (b) a test named `test_r{n}_{slug}` whose docstring cites the R-number **and** the legal source (Directive article, CJEU case, Regulation); (c) a row in `docs/transport/rules.md` mapping R-number → module → test → legal source.
4. **A G-task PR that does not include its R-test does not merge.**
5. All fixtures come from `tests/factories/transport.py`.
6. Any doubt about whether a value is real: **treat it as real.**

**Step 6 — raise the separate legal item.** Add to `docs/DECISIONS-NEEDED.md`: the Fleet Fuel repository's own git history retains this data regardless of anything done here; its retention, redaction or destruction is a decision for counsel with an owner and a date. **Report it; do not act on it.**

### Invariants this order must preserve

- §10 prohibition: no PII, no real identifiers, no secrets in code, tests or fixtures.
- The scan must not itself leak the values it protects (hence hashing).
- CI must **fail closed**: if the deny-list file or the salt is missing, the job fails rather than silently passing.

### Database / migration impact

None.

### Testing requirements

`backend/tests/test_pii_scan.py`
- `test_scan_is_clean_on_the_current_tree` — the repo passes today.
- `test_scan_detects_a_seeded_violation` — write a temp file containing a synthetic-but-deny-listed token into a temp dir, scan it, assert non-zero exit and the **label** (not the value) in the output.
- `test_scan_detects_a_structural_vat_id`.
- `test_scan_detects_a_valid_iban_literal` and `test_scan_ignores_an_invalid_iban_lookalike` (proves the MOD-97 filter keeps false positives down).
- `test_scan_fails_closed_when_the_denylist_is_missing`.
- `test_synthetic_ibans_pass_mod97` — the fixture factory produces IBANs `core/bank_id.is_valid_iban` accepts.
- `test_synthetic_vat_ids_are_not_on_the_denylist`.

### Acceptance criteria

- [ ] `python scripts/pii_scan.py --tree` exits 0 on the current repo and non-zero on a seeded violation.
- [ ] The deny-list contains **no plaintext** real identifier.
- [ ] The CI job runs on `pull_request` and is documented as a required check; a nightly history scan exists.
- [ ] `git log -S` / all-refs scan for the structural patterns returns nothing in Bid_it history.
- [ ] `docs/transport/harvest-protocol.md` exists and is referenced from `docs/transport/rules.md`.
- [ ] `tests/factories/transport.py` exists with synthetic generators and passing self-tests.
- [ ] The counsel item is recorded in `docs/DECISIONS-NEEDED.md` with an owner field.
- [ ] Zero feature code was written.

### Rollback strategy

Removing the scan is a *security* rollback and must never be done to unblock a build. If the scan produces a false positive, add the specific value to the allow-list **with a justification comment naming who verified it is synthetic** — never disable the job.

### Documentation to update

`docs/transport/harvest-protocol.md`, `docs/transport/rules.md`, `docs/DECISIONS-NEEDED.md`, and a line in `docs/DEPLOYMENT.md` about the CI salt secret.

### Self-verification block

```bash
cd /home/user/Bid_it
python scripts/pii_scan.py --tree ; echo "tree exit: $?"
python scripts/pii_scan.py --history 2>&1 | tail -20 ; echo "history exit: $?"
grep -rIn -E '\b(EE|LT|LV|PL|CZ)[0-9]{8,12}\b' backend/ frontend/ docs/ --exclude-dir=node_modules || echo "no structural VAT ids"
cd backend && . .venv/bin/activate && python -m pytest tests/test_pii_scan.py -q && python -m pytest -q
```

<!-- ═══════════════ END: WO-6 ═══════════════ -->

---

## WO-7 — One validation engine

<!-- ═══════════════ COPY FROM HERE: WO-7 ═══════════════ -->

**WORK ORDER 7 — One validation engine (board C1.1). Effort 3–5 days. Priority P0. Depends on: nothing. Blocks WO-E1.1 (capture-review UI) and G3.3.**

### Objective and business value

Two validators disagree about whether an invoice reconciles:

- `backend/app/services/validation.py` — 14 deterministic checks, **advisory**, org-toggled, findings persisted as JSON, tolerances `0.01` money / `0.02` tax / `max(0.01, 1%)` per line.
- `backend/app/api/routes/invoice_review.py::_reconcile` — always-on, **zero-tolerance**, **blocking** at `POST /invoices/{id}/submit`, and it lives **in a controller**, violating `docs/architecture/engineering-rules.md` §3 and the charter's "business logic never in controllers".

You cannot sell "audit-ready" while two engines give different answers to the same question. And the capture-review UI (the highest-revenue item in the roadmap) needs **one** findings shape to render.

### Scope

**In scope:** merge into `app/services/validation.py` as one rule registry, each rule carrying an explicit `block | advise` policy and its own tolerance; the route calls the service; no rule exists twice.

**Out of scope:** changing what any rule *decides* (this is a behaviour-preserving move), duplicate-detection improvements (E1.4), and adding new rules. **If you find yourself changing a threshold, stop — that is a different work order.**

### Files to touch

`backend/app/services/validation.py`, `backend/app/api/routes/invoice_review.py`, `backend/app/api/routes/invoices.py`, `backend/app/schemas/validation.py`, `backend/tests/test_validation.py`, `backend/tests/test_invoice_review_e2e.py`, `backend/tests/test_reconcile_characterisation.py` (**new**).

### Implementation guidance — order matters

**Step 1 — characterise before you move (mandatory, do this first and commit it separately if you can).**
Write `backend/tests/test_reconcile_characterisation.py` pinning `_reconcile`'s **current** behaviour at the **route** level, through `POST /api/v1/invoices/{id}/submit`:
- per-line `tax_rate < 0` → 422; `tax_rate > 100` → 422;
- `amount != q2(quantity × unit_price)` → 422, message naming the line description;
- header `subtotal` mismatch → 422; `tax_amount` mismatch → 422; `total` mismatch → 422;
- zero line items → 422;
- a perfectly reconciling invoice → success, with the exact `ReconciliationOut` field values (`computed_subtotal`, `computed_tax`, `computed_total`, `stated_*`, `balanced`, `findings`).
Run them **against the unchanged code** and confirm green. These tests are the contract for the refactor.

**Step 2 — the rule registry.** In `app/services/validation.py`, model each check as a record:

```python
@dataclass(frozen=True)
class Rule:
    code: str            # stable, unique, machine-readable slug
    policy: Literal["block", "advise"]
    tolerance: Decimal   # explicit per rule — never a module-level constant reused by accident
    scope: Literal["header", "line", "document"]
```

The `_reconcile` checks enter as `policy="block"` with `tolerance=Decimal("0")`. The existing 14 advisory checks keep their tolerances and `policy="advise"`.

**Step 3 — the evaluator** returns a single result carrying `findings: list[{severity, code, message, field}]` (the existing persisted shape — **do not change it**; the SPA reads it) plus a derived `blocking: list[Finding]`. The submit gate refuses when `blocking` is non-empty.

**Step 4 — the route becomes thin.** `invoice_review.py` imports the service, calls it, maps a blocking result to **422** with the same messages the characterisation tests pinned, and shapes `ReconciliationOut` from the service's computed values. `_reconcile` is deleted from the route module. `app/services/validation.py` must not import `app.api` (boundary test).

**Step 5 — registry integrity test.** Assert no duplicate `code`, every rule has a non-empty `code`, every `block` rule has a tolerance of exactly `Decimal("0")` unless a docstring justifies otherwise, and the set of codes is stable (snapshot the sorted list in the test so an accidental rename is visible).

**Step 6 — preserve the two org toggles exactly**: AI-validation-advisory and human-validation-routing-to-`pending`. With neither on, an invoice's validation status stays `none`. Assert all three combinations.

### Invariants this order must preserve

- §3 layering: business logic leaves the controller; `services` never import `app.api`.
- §4.9/4.10: money stays `Decimal` via `core/money.q2`; the server recomputes totals — a client-supplied total is compared, never trusted.
- §4.20 wire contract: identical status codes and message text as before. This refactor is **invisible over the wire**.
- Advisory findings still persist as JSON `{severity, code, message, field}`.

### Database / migration impact

None expected. If a `code` is added to persisted findings that was previously absent, existing rows must still deserialise — write a defensive read (missing `code` → `"legacy"`), and say so.

### Testing requirements

- `tests/test_reconcile_characterisation.py` — as above, **passing before and after, unmodified**.
- `tests/test_validation.py::test_rule_registry_has_no_duplicate_codes`.
- `tests/test_validation.py::test_block_rules_have_zero_tolerance`.
- `tests/test_validation.py::test_rule_codes_snapshot` — the sorted code list matches an inline expected list.
- `tests/test_validation.py::test_advisory_findings_shape_unchanged` — `{severity, code, message, field}`.
- `tests/test_validation.py::test_toggle_matrix` — parametrised over (ai on/off × human on/off): resulting status ∈ `{none, flagged, pending}` exactly as today.
- `tests/test_validation.py::test_no_rule_is_implemented_twice` — assert `app/api/routes/invoice_review.py` no longer contains a reconciliation implementation (an AST or source scan for the removed helper is acceptable and cheap).
- `tests/test_boundaries.py` unmodified and green.
- `tests/test_invoice_review_e2e.py` unmodified and green.

### Acceptance criteria

- [ ] `_reconcile` no longer exists in a route module.
- [ ] Every characterisation test passes unchanged after the move.
- [ ] One registry; no duplicate rule codes; every rule declares `block | advise` and a tolerance.
- [ ] Advisory persistence shape unchanged; the SPA needs no change.
- [ ] The three toggle combinations behave exactly as before.
- [ ] `tests/test_boundaries.py` green unmodified.
- [ ] Full suite green; `mypy app` clean.

### Rollback strategy

PR revert; no schema. Because the change is contract-preserving, a revert cannot strand data. If a discrepancy is found in production, the safe intermediate step is to flip the offending rule from `block` to `advise` **in the registry** (one line, audited by the config change) rather than reverting the whole refactor — but only with an explicit decision recorded, because it weakens a gate.

### Documentation to update

- `docs/architecture/adr/0025-…` (written in WO-10) is where the decision is recorded; add a stub reference now.
- `docs/architecture/domain-modules.md` — validation is a single service-owned engine.
- `docs/architecture/engineering-rules.md` — cite this as the worked example of "no business logic in controllers".

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
git stash list  # ensure the characterisation tests were green BEFORE the move — say so in the report
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/test_reconcile_characterisation.py -q
python -m pytest tests/test_validation.py tests/test_invoice_review_e2e.py tests/test_boundaries.py -q
python -m pytest -q
grep -n "_reconcile" app/api/routes/*.py || echo "no reconcile logic left in a route module"
```

<!-- ═══════════════ END: WO-7 ═══════════════ -->

---

## WO-8 — One FX convention, no silent cross-currency sums, scheduled refresh

<!-- ═══════════════ COPY FROM HERE: WO-8 ═══════════════ -->

**WORK ORDER 8 — One FX convention (board C1.2 + C1.3 + C1.4). Effort 7–10 days. Priority P0. Depends on: nothing.**

### Objective and business value

Three defects that are already producing wrong money **in a bank file**:

1. **Two FX conventions.** ECB rates are *units per 1 EUR*; the invoice path **divides**; the expense item path **multiplies**. `fx_source` on an expense item is unvalidated free text.
2. **Silent cross-currency sums.** `app/services/ap_aging.py::summarize` adds `it.outstanding` across currencies with no conversion. `reimbursement.eur_of` and `payment_run.eur_of` fall back to the raw foreign `total` and then label the sum EUR — **and the SEPA file emits `Ccy="EUR"`**.
3. **No scheduled ECB refresh.** Rates update only when an admin calls `POST /fx/refresh`.

Business value: a foreign amount labelled EUR in a pain.001 is a payment sent for the wrong value. This is the fastest way to lose a customer and the hardest to explain afterwards.

### Scope

**In scope**
- ECB convention (divide) canonical everywhere; fix the expense path with a data migration that records what it changed.
- `fx_source` a validated enum `{eur, stated, ecb, unknown}` on every model carrying it.
- Every aggregate converts with a recorded rate or refuses per-currency.
- A daily ECB refresh job on the existing scheduler.
- FI-15 in `tests/test_money_invariants.py`.

**Out of scope**
- FX gain/loss revaluation (explicitly deferred; if it comes up, record it in `docs/DECISIONS-NEEDED.md`).
- One currency registry (C1.5) and multi-currency reporting across analytics/benchmark/budget (C1.7) — later orders.

### Files to touch

`backend/app/services/fx.py`, `app/services/expenses.py`, `app/models/expense.py`, `app/schemas/expense.py`, `app/services/ap_aging.py`, `app/services/reimbursement.py`, `app/services/payment_run.py`, `app/services/sepa.py`, `app/services/scheduler.py`, `app/services/job_handlers.py`, a migration + data migration, `backend/tests/test_money_invariants.py`, `tests/test_fx.py`, `tests/test_expenses.py`, `tests/test_ap_aging.py`, `tests/test_reimbursement_sepa.py`.

### Implementation guidance

**Step 1 — pin the current invoice-path behaviour** with a characterisation test before changing anything: for a set of `(amount, currency, date)` triples, record the EUR figure the invoice path produces today. That is the target the expense path must converge on.

**Step 2 — one conversion function.** `app/services/fx.py` already exposes `resolve_rate`, `rate_for`, `to_eur`, `eur_total`. Make **`to_eur` the only conversion entry point** used by every caller, and document the convention in its docstring in one sentence: *"ECB publishes units of `currency` per 1 EUR; EUR = amount / rate; EUR itself has rate 1."* Rate selection is the most recent rate **on or before** the transaction date (a Sunday transaction uses Friday's rate) — assert that.

**Step 3 — fix the expense path.** Replace the multiply with `fx.to_eur`. Preserve the existing correct rule: **`fx_source == "unknown"` yields `total_eur = None`, never a guessed number.**

**Step 4 — `fx_source` as an enum.** Add a Python `enum` in the model layer (or `core`) and validate at the schema boundary. Migrate existing free-text values with an explicit mapping table in the data migration; anything unmappable becomes `unknown` (and therefore `total_eur = None`) — **never guess**. The migration must **log/report the counts per bucket**.

**Step 5 — data migration for previously-wrong values.** Recompute `total_eur` for affected expense items. **Do not silently overwrite:** write the old value into a migration-emitted report (row id, old, new) and — because financial history matters — consider writing an audit event per corrected row (`expense.fx_corrected`, meta old→new). Amounts that a human approved at a wrong EUR value are a **business** issue: flag them in the report and raise them in `docs/DECISIONS-NEEDED.md` rather than deciding unilaterally whether to restate an approved report.

**Step 6 — no silent cross-currency sums.**
- `ap_aging.summarize`: either return per-currency buckets, or convert with a recorded rate. Given the AR reports already do this correctly (single-currency per report), **follow that pattern**: a currency filter, never a mixed sum. Changing the response shape is a **wire-contract change** — update the schema, the SPA and the tests together, and call it out in the report.
- `reimbursement.eur_of` / `payment_run.eur_of`: remove the raw-total fallback. A line that cannot convert makes the run **refuse** with a clear message naming the line and the missing rate.
- `sepa.build_pain001`: never emit `Ccy="EUR"` for an unconverted foreign amount. Emit the actual currency, or refuse. **Refusing is correct** — a wrong payment is worse than a blocked one.

**Step 7 — scheduled refresh.** `app/services/scheduler.py` has `DAILY_KINDS = (job_handlers.RECURRING_GENERATE, job_handlers.DUNNING_RUN, job_handlers.AP_DUE_ALERTS)` and `enqueue_daily(db, today=...)` keyed `f"{kind}:{today.isoformat()}"` per org. Add an `FX_REFRESH` kind and a handler calling `fx.refresh_from_ecb`. Two design points:
- ECB rates are **global, not per-tenant.** Enqueuing one job per org would fetch the same data N times. Either enqueue it once with a global idempotency key, or make the handler a no-op when the day's rates are already loaded. **Pick one, justify it in the docstring, and test it.**
- Preserve the existing graceful degradation: the 12s timeout, never raising into the scheduler, and cached rates continuing to serve on failure.

### Invariants this order must preserve

- §4.14 no cross-currency sums without a recorded conversion; §4.15 one convention, `unknown → NULL`.
- §4.9 Decimal only. **Never** introduce a float division here.
- §4.10 server recomputes.
- FI-11 (FX provenance always in the enum) and the new **FI-15**.
- `tests/test_fx_europe.py`'s indicative-vs-ECB distinction (12 non-ECB-published currencies flagged `indicative`) must survive untouched.

### Database / migration impact

- `expense_items.fx_source` (and any other model carrying it) becomes constrained — an enum or a `CHECK`.
- A data migration recomputing `total_eur` on affected rows, with a printed report.
- No new tenant table, so no new RLS policy — **verify** that assumption with `test_rls.py`.
- The downgrade must restore the previous free-text column type without destroying values.

### Testing requirements

`tests/test_fx.py`
- `test_ecb_convention_divides` — a known rate, a known amount, an exact expected EUR figure.
- `test_rate_selection_uses_most_recent_on_or_before` — a Sunday transaction resolves to Friday's rate.
- `test_missing_rate_yields_none_not_a_guess`.
- `test_invalid_fx_source_rejected_at_write` → 422.

`tests/test_money_invariants.py`
- **`test_fi15_no_aggregate_sums_across_currencies_without_conversion`** — build a fixture with EUR + SEK + PLN outstanding; assert the aggregate either (a) returns per-currency buckets, or (b) returns a single figure **with a recorded rate per component**; assert it never returns a bare sum of the raw amounts.
- `test_invoice_path_and_expense_path_agree` — property-style over a matrix of `(amount, currency, date)`, both paths produce the identical `Decimal`.
- Keep the existing money-boundary assertions and add the Fleet Fuel smoke cases: `q2(Decimal("399.994")) < 400`, `q2(Decimal("399.995")) >= 400`.

`tests/test_reimbursement_sepa.py` / `tests/test_sepa.py`
- `test_mixed_currency_run_is_refused_when_a_line_cannot_convert` — names the line and the missing rate.
- `test_sepa_never_labels_a_foreign_amount_eur` — parse the generated XML; for every `<Amt Ccy="...">`, assert the currency matches the source amount's currency.

`tests/test_jobs.py` or a new `tests/test_fx_schedule.py`
- `test_daily_fx_refresh_is_enqueued_once_per_day` — calling `enqueue_daily` a hundred times yields one live FX job for the day.
- `test_fx_refresh_handler_never_raises_into_the_scheduler` — patch the fetch to raise; assert the job records a failure and the scheduler continues.
- `test_cached_rates_still_serve_after_a_failed_refresh`.

Migration test: extend `tests/test_migrations.py` with a case seeding a pre-migration expense row with a multiplied `total_eur` and a free-text `fx_source`, running the migration, and asserting the corrected value and the mapped enum.

### Acceptance criteria

- [ ] The same `(amount, currency, date)` yields the identical EUR figure through the invoice path and the expense path.
- [ ] `fx_source` is an enum everywhere; an invalid value is refused at write; `unknown` yields `NULL`.
- [ ] No aggregate sums across currencies without a recorded conversion (FI-15 green).
- [ ] No export or bank file labels a foreign amount EUR; a non-convertible run is refused with a clear message.
- [ ] A daily ECB refresh runs, is idempotent per day, and never raises into the scheduler.
- [ ] The data migration prints a per-bucket report and did not silently discard any value.
- [ ] Full suite green; `mypy app` clean; `alembic check` clean; single head.

### Rollback strategy

The **data migration is the risk**, not the code. Requirements:
- The downgrade restores the column type and does **not** attempt to re-multiply values (that would compound the error). It leaves corrected values in place and says so in its docstring.
- Before running in production, take a database snapshot and keep the migration's printed report as the reconciliation artifact.
- If a rollback is needed after the data migration ran, roll back the **code** only; corrected `total_eur` values are *more* correct and must stay.

### Documentation to update

`docs/architecture/adr/0010-money-tax-fx.md` — restate the single convention and the `unknown → NULL` rule; `docs/architecture/data-model.md` for the enum; `docs/DECISIONS-NEEDED.md` for the restatement question on already-approved expense reports.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic upgrade head && alembic check
python -m pytest tests/test_fx.py tests/test_fx_europe.py tests/test_money_invariants.py -q
python -m pytest tests/test_expenses.py tests/test_ap_aging.py tests/test_reimbursement_sepa.py tests/test_sepa.py -q
python -m pytest tests/test_migrations.py tests/test_jobs.py -q
python -m pytest -q
grep -rn "\* rate\|rate \*" app/services/ | grep -vi "test" || echo "no multiply-by-rate paths left"
```

<!-- ═══════════════ END: WO-8 ═══════════════ -->

---

## WO-9 — Payment-run controls

<!-- ═══════════════ COPY FROM HERE: WO-9 ═══════════════ -->

**WORK ORDER 9 — Payment-run controls: maker ≠ checker, export-once, unique MsgId, skipped payees (board D1.1 + D1.2 + D1.3). Effort 7–9 days. Priority P0. Depends on: WO-1, WO-2.**

### Objective and business value

The run itself already has five good double-payment guards (pool exclusion, create-time check, `open`-only gate, optimistic `version`, `SELECT … FOR UPDATE`). What is missing are the **controls an auditor asks about**:

- **No segregation of duties.** One user holding `PAYMENT_WRITE` can create, approve and mark a run paid.
- **The bank-file GETs are unguarded.** `GET /{run_id}/export` and `GET /{run_id}/sepa` in `backend/app/api/routes/payment_runs.py` carry **no `PAYMENT_WRITE`**, no already-exported flag, and work on an **unpaid** run.
- **Deterministic `MsgId`.** `app/services/sepa.py:133` sets `msg_id=f"RUN-{run.id[:8]}"`, so a re-export sends a **duplicate message id** to the bank — banks reject or, worse, deduplicate silently.
- **Silently skipped payees.** `sepa.payment_run_sepa` returns `(xml, skipped)` and the count travels in an `X-Skipped` header the route discards. **The treasurer is never warned that a supplier was dropped from the file.**

### Scope

**In scope:** SoD on pay; permission + state + export-once + unique `MsgId` on both export routes; surfacing skipped payees and requiring acknowledgement; the same treatment for reimbursement batches.

**Out of scope:** per-creditor aggregation and forward-dated execution (D1.4), payment-run selection intelligence (D1.5). Do not start them.

### Files to touch

`backend/app/services/payment_run.py`, `app/api/routes/payment_runs.py`, `app/api/routes/reimbursements.py`, `app/services/sepa.py`, `app/services/reimbursement.py`, `app/models/payment_run.py`, a migration, `frontend/src/pages/PaymentRuns.tsx` and `Reimbursements.tsx`, tests below.

### Implementation guidance

**Step 1 — maker ≠ checker.** Mirror the SoD patterns already in the codebase (`invoice_review.py::_guard_decider`: `Invoice.submitted_by` cannot approve/reject/return; expenses: "You cannot approve your own expense report").
- Record `created_by` and `approved_by` on the run (add columns if absent).
- `POST /{run_id}/pay` refuses when `current.id in {run.created_by, run.approved_by}` → **403** with `code="maker_is_checker"` and a message naming the control.
- A **platform-admin exemption** may exist, but it must be **explicit and audited** (`payment_run.sod_override`, meta naming the overridden control). Do not make it silent.

**Step 2 — export guard.**
- Require `PAYMENT_WRITE` on both export routes (declare it via the WO-1 dependency, not an in-handler call).
- Refuse unless the run is in an approved/paid state — the exact allowed set must match the existing lifecycle in `payment_run.py`; read it, do not assume.
- Add `exported_at: datetime | None` and `export_count: int` (default 0). A second export requires an explicit confirmation flag in the request (e.g. `?confirm_reexport=true`) → otherwise **409** with `code="already_exported"` and the timestamp of the first export.
- Audit every export: `payment_run.exported` with `export_count`, the `MsgId`, the payee count and the total.

**Step 3 — unique `MsgId` per generation.** Replace `f"RUN-{run.id[:8]}"` with a value unique per *generation*, not per run, and traceable back to the run — e.g. `f"RUN-{run.id[:8]}-{export_count+1}-{uuid4().hex[:8]}"` bounded to the pain.001 `MsgId` max length (35 characters — **check and enforce the limit**, do not overflow). Store the emitted `MsgId` on the run (or on an export-history row) so a bank query can be answered later. Assert two consecutive exports produce different ids.

**Step 4 — surface skipped payees.** Change `payment_run_sepa` to return the **payee identities**, not just a count. The route puts them in the **response body**, not a header. Exporting a run containing any skipped payee is **refused** unless the caller passes an acknowledgement (e.g. `acknowledge_skipped=true`), and the refusal **names** the payees. Apply the same to reimbursements — employees without an IBAN are silently skipped there too.

Combine with WO-2: after this order, a payee is skipped only for a *missing* IBAN, because an *invalid* one is refused at write and inside `build_pain001`.

**Step 5 — frontend.** `PaymentRuns.tsx`: show `exported_at` / `export_count`; a re-export goes through `ConfirmDialog` explaining that the bank will see a new message id; skipped payees are listed by name with an explicit "I understand these will not be paid" acknowledgement before the download is enabled. Same for `Reimbursements.tsx`.

### Invariants this order must preserve

- §4.8 SoD; §4.11–4.13 ledger correctness (this order must not change how money is allocated — only who may do it and when a file may be produced).
- The five existing double-payment guards must remain; add a test asserting each is still exercised.
- §4.16 audit on every export and every pay.
- §4.20 wire contract: adding fields to a response body is compatible; **removing the `X-Skipped` header is not** unless nothing consumes it — grep the frontend and tests first and report what you found.

### Database / migration impact

Migration adding to `payment_runs`: `created_by`, `approved_by` (if absent), `exported_at`, `export_count NOT NULL DEFAULT 0`, `last_msg_id`. Consider a `payment_run_exports` child table instead of `last_msg_id` if you want full export history — **if you add a table it is tenant-scoped and needs an RLS policy in the same migration**. Backfill: `export_count = 0`, `exported_at = NULL` (existing runs are treated as never exported; state this in the migration docstring because it means an already-sent file can be re-exported once without confirmation — flag that to the operator in the deploy note).

### Testing requirements

`backend/tests/test_payment_runs_sod.py` (**new**)
- `test_creator_cannot_mark_run_paid` → 403, `code="maker_is_checker"`.
- `test_approver_cannot_be_the_payer`.
- `test_different_user_with_payment_write_can_pay` → 200.
- `test_platform_admin_override_is_audited` — the override event exists with the control named.

`backend/tests/test_payment_run_export_guard.py` (**new**)
- `test_export_requires_payment_write` — a `PAYMENT_READ`-only role gets 403 on both `/export` and `/sepa`.
- `test_export_refused_on_an_unapproved_run` → 409.
- `test_second_export_requires_confirmation` → 409 `already_exported`, then success with the confirm flag.
- `test_two_exports_produce_distinct_msg_ids` — parse both XMLs, assert `MsgId` differs and both are ≤35 chars.
- `test_export_is_audited_with_msgid_and_totals`.
- `test_export_blocked_when_a_payee_has_no_iban` — refused, response body **names** the payee; with `acknowledge_skipped=true` it succeeds and the audit event records who was skipped.
- `test_reimbursement_batch_applies_the_same_rules`.
- `test_existing_double_payment_guards_still_hold` — re-assert the five guards (a run cannot include an invoice already in an open run; the `FOR UPDATE` path; the stale-`version` 409).

Real-Postgres concurrency (add to the postgres CI job's file list if appropriate): `test_two_concurrent_pays_settle_once`.

### Acceptance criteria

- [ ] The creator/approver marking a run paid gets 403 with a clear message.
- [ ] Both export routes require `PAYMENT_WRITE` and refuse an unapproved/unpaid run.
- [ ] A second export requires explicit confirmation and produces a **different** `MsgId` (≤35 chars).
- [ ] Skipped payees are named in the response body and the UI, and block the export until acknowledged.
- [ ] Every export and pay is audited with actor, totals and `MsgId`.
- [ ] The five pre-existing double-payment guards still pass.
- [ ] Full suite green; `mypy app` clean; migration single-head and reversible.

### Rollback strategy

Code revert is safe. The migration is additive; the downgrade drops the new columns and therefore **loses export history** — state that explicitly and require an operator to export the `payment_run_exports` rows (or the `last_msg_id` values) to a CSV before downgrading. Never downgrade this in production without that extract, because the `MsgId` values may be needed to trace a payment with the bank.

### Documentation to update

`docs/architecture/domain-modules.md` (settlement controls), `docs/security/authorization-policy-matrix.md` (export requires `PAYMENT_WRITE`; pay requires a different user), and a short runbook note in `docs/DEPLOYMENT.md` on what a re-export means to the bank.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic upgrade head && alembic check
python -m pytest tests/test_payment_runs.py tests/test_payment_runs_sod.py tests/test_payment_run_export_guard.py -q
python -m pytest tests/test_sepa.py tests/test_reimbursement_sepa.py tests/test_ap_payments.py -q
python -m pytest -q
grep -rn "X-Skipped" app/ ../frontend/src/ || echo "no consumers of the discarded header remain"
cd ../frontend && npm run build
```

<!-- ═══════════════ END: WO-9 ═══════════════ -->

---

## WO-10 — Documentation truth-up, the new ADRs, and the M0 exit gate

<!-- ═══════════════ COPY FROM HERE: WO-10 ═══════════════ -->

**WORK ORDER 10 — Documentation truth-up, ADRs 0023–0026, tenancy parity test, M0 exit gate (board J1.1 + J1.2 + B1.3). Effort 5–7 days. Priority P0. Depends on: WO-1…WO-9 for the exit gate; the docs and the parity test may start earlier.**

### Objective and business value

With a bus factor of one, **a lying document is worse than no document**. `README.md` and `ARCHITECTURE.md` describe a ~12-test analytics MVP against a ~32k-LOC platform with ~761 tests; `docs/architecture/data-model.md` marks as "target/not built" several things that now exist (`payments`, `customers`, `tax_codes`, approval policies). Four decisions taken in this plan have no ADR. And M0 needs a **gate that proves it is done**, not an opinion that it is.

### Scope

**In scope**
1. Regenerate or delete `README.md` + `ARCHITECTURE.md`; correct `data-model.md`'s stale markers.
2. Write ADR-0023 … ADR-0026.
3. `backend/tests/test_tenancy_parity.py` (behavioural isolation over the **real query path**).
4. `backend/tests/test_ai_policy.py` (zero external calls at defaults).
5. `docs/M0-exit-gate.md` — each M0 exit criterion mapped to the test or artifact that proves it.

**Out of scope:** touching the 22 existing ADRs or `docs/product/*` — they are the specification and stay.

### Files to touch

`README.md`, `ARCHITECTURE.md`, `docs/architecture/data-model.md`, `docs/architecture/adr/0023-platform-evolution-and-transport-seam.md`, `0024-structural-authorization.md`, `0025-one-validation-engine-one-fx-convention.md`, `0026-ai-capture-policy.md`, `docs/architecture/adr/README.md` (index), `backend/tests/test_tenancy_parity.py`, `backend/tests/test_ai_policy.py`, `docs/M0-exit-gate.md`.

### Implementation guidance

**Step 1 — docs truth-up.** Prefer **regenerating** over deleting, but only if you can make it true cheaply. `README.md` must state: what the product is; the real stack; the real commands (from the `Makefile`); how to run tests; where the specification lives (`docs/architecture/adr/`, `docs/product/`). `ARCHITECTURE.md` either becomes a one-page pointer to `docs/architecture/overview.md` + the ADR index, or is deleted with that pointer left in `README.md`. In `data-model.md`, walk every "target / not built" marker and check it against `backend/app/models/` — correct each one. **Do not describe anything as done that you have not verified in the code.**

**Step 2 — the four ADRs.** Use the existing ADR format in `docs/architecture/adr/0001-modular-monolith.md`. Content, condensed from the plan:

- **0023 — Platform evolution + the transport-vertical seam.** The 8 bounded contexts + 2 projection layers; Integrations is a *register of adapters*, not a context; Dashboard and Reports are *projections* and must not fork the math. The transport seam's six binding rules (owns only transport tables; reads core through services; is an entitlement `transport` default-off; reuses the platform floor; adds permissions not roles; never gates a core figure and is never gated by one). **And the translation of the Fleet Fuel isolation rule:** rather than a physically separate database, a claim **materialises and freezes its own lines at submission**, `fuel_transactions` rows locked into a submitted claim are protected by a `RESTRICT` FK plus a period-delete guard, and the close is a durable idempotent job. Explain why this is *strictly stronger* than the original.
- **0024 — Structural authorization.** Delivered in WO-1. The router dependency, `PUBLIC_ROUTES` with reasons, the both-directions CI assertion, and the standing policy: **raise the fixture's role, never lower the assertion.**
- **0025 — One validation engine, one FX convention, one currency registry, one dimension registry.** Delivered partly in WO-7 and WO-8; state the remaining commitments (C1.5, C1.6, C1.7) as accepted-not-yet-implemented so the decision is not re-litigated.
- **0026 — AI capture policy (no model wired yet).** Opt-in, default-off, advisory, strict (never invents a field), best-effort (falls back to the deterministic chain), an **independent** verifier treating the source document as truth, and a DLP classification gate persisting `{type, count}` findings and **never the matched value**, failing **open** on a scan error and **closed** only when a policy is set and exceeded. State the acceptance test: **with all AI settings at defaults, the system runs end to end with zero external calls.**

**Step 3 — `tests/test_tenancy_parity.py` (this is the real engineering in this order).**

For **every tenant-scoped table reachable by a route**:
1. Seed tenant A and tenant B with **overlapping** data — the same invoice numbers, the same vendor names, the same amounts. Overlap is the point; non-overlapping data can pass a broken filter by luck.
2. Bind tenant A's context and call the **real query path the route uses** — the HTTP route or the service function. **Never a hand-written `select()`**; a hand-written query tests your test, not the app.
3. Assert A's rows are present and **zero** of B's are.
4. Mirror with B bound.
5. Additionally assert: a cross-tenant **fetch by id returns 404, never 403** (§4.4).

Build the table list **programmatically** from the tenant-model registry that `tests/test_rls.py` already uses, so a newly added tenant table is automatically in scope. Where a table has no route-reachable read path, assert that explicitly in a documented exemption list — and assert the exemption list has no stale entries (same both-directions discipline as WO-1).

Also assert the guard's lifecycle: the tenant ContextVar is **reset at both ends of a request** so nothing leaks between requests (`test_tenant_context_does_not_leak_between_requests`).

**Step 4 — `tests/test_ai_policy.py`.** With default settings, run a representative end-to-end flow (upload → parse → confirm) with the network **blocked at the socket level** (monkeypatch `socket.socket` / the HTTP client to raise) and assert the flow completes. This is the executable form of the "zero external calls" promise.

**Step 5 — `docs/M0-exit-gate.md`.** A table: each M0 exit criterion → the test file/name or the artifact that proves it → current status. Criteria to include (from the plan): route authorization coverage both directions; vendor bank-detail control; partners lockdown; org status per request; mandatory inbound-email secret; one validation engine; one FX convention + no cross-currency sums + scheduled ECB refresh; payment-run maker≠checker + export guard + unique MsgId + surfaced skips; `users.org_id` dual-write resolved (**note: this is B1.5 and is NOT in WO-1…WO-10 — flag it as an open M0 item**); PII quarantine + harvest protocol; README/ARCHITECTURE truthful; baseline tests plus the new coverage/parity tests green.

### Invariants this order must preserve

- §4.1–4.5 tenancy — the parity test *proves* them; it must not be weakened to pass. **If it finds a leak, that is a release blocker: stop and report, do not adjust the test.**
- §4.19 the AI policy is documented before any model exists.
- No existing ADR is edited except the index.

### Database / migration impact

None.

### Testing requirements

- `tests/test_tenancy_parity.py::test_every_scoped_table_isolates_via_the_real_query_path` (parametrised per table).
- `tests/test_tenancy_parity.py::test_cross_tenant_fetch_by_id_returns_404_not_403`.
- `tests/test_tenancy_parity.py::test_exemption_list_has_no_stale_entries`.
- `tests/test_tenancy_parity.py::test_tenant_context_does_not_leak_between_requests`.
- `tests/test_tenancy_parity.py::test_parity_check_catches_a_deliberately_unscoped_query` — the self-test: monkeypatch one service function to drop its `org_id` filter inside the test and assert the checker fails. **A test that cannot fail proves nothing.**
- `tests/test_ai_policy.py::test_defaults_make_zero_external_calls`.
- Docs check: a cheap `tests/test_docs_truth.py` asserting `README.md` does not contain the stale strings (e.g. "12 tests") is optional but cheap insurance — propose it, do not over-build it.

### Acceptance criteria

- [ ] No document in the repo contradicts the code (state which documents you verified and how).
- [ ] ADRs 0023–0026 merged, added to the ADR index, and each referenced by the work order that implements it.
- [ ] `tests/test_tenancy_parity.py` covers every tenant-scoped table (or names it in a justified exemption), fails on a deliberately unscoped query, and is documented as a required CI check.
- [ ] Cross-tenant fetch by id returns 404 everywhere tested.
- [ ] `tests/test_ai_policy.py` passes with the network blocked.
- [ ] `docs/M0-exit-gate.md` exists, maps every criterion to a proof, and honestly marks the unfinished ones (notably B1.5).
- [ ] Full suite green; `mypy app` clean.

### Rollback strategy

Documentation and tests only — a PR revert is complete and harmless. The one caution: **never revert the parity test to unblock a release.** If it goes red, the code is wrong.

### Documentation to update

All of the above, plus add the new required checks (`test_authz_coverage.py`, `test_tenancy_parity.py`, `pii-scan`) to the CI required-check list in `docs/DEPLOYMENT.md`.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/test_tenancy_parity.py tests/test_ai_policy.py -q
python -m pytest tests/test_rls.py -q
RLS_TEST_DATABASE_URL=postgresql+asyncpg://appuser:apppw@localhost:5432/invoiceiq \
  python -m pytest tests/test_rls.py tests/test_numbering_concurrency.py -q
python -m pytest -q
cd .. && grep -rn "12 tests\|analytics MVP" README.md ARCHITECTURE.md || echo "no stale claims left"
ls docs/architecture/adr/002[3-6]*.md && cat docs/M0-exit-gate.md
```

<!-- ═══════════════ END: WO-10 ═══════════════ -->

---
# PART C — WORK-ORDER TEMPLATE

Use this to generate every future work order from the roadmap (`ARCH_plan.md` §4 epics A–J, §9 TODO board, §3 milestones M0–M6). Fill every field; an empty field means the order is not ready to hand over.

<!-- ═══════════════ COPY FROM HERE: WORK-ORDER TEMPLATE ═══════════════ -->

**WORK ORDER <n> — <short title> (board <ids>). Effort <S 1–2d | M 3–5d | L 6–12d | XL 13–25d>. Priority <P0|P1|P2|P3>. Milestone <M0…M6>. Depends on: <WO ids or "nothing">.**

### Objective and business value
<Two paragraphs. First: the defect or gap, stated with the *verified* evidence — file, symbol, line — not a generality. Second: who pays more, churns less, or stops losing money because of it. If you cannot write the second paragraph, the order is not worth doing yet.>

### Scope
**In scope:** <bulleted, concrete, each item mapping to a file or a test>
**Out of scope:** <bulleted — name the adjacent work this order must NOT start, with the board id that owns it. This is the anti-scope-creep clause; it is not optional.>

### Files to touch
| File | Change |
|---|---|
| `<exact path>` | <what changes> |
> Every path must exist (or be explicitly marked **new**). Verify before handing over.

### Implementation guidance
<Numbered steps, in execution order. For a behaviour-preserving refactor, step 1 is ALWAYS "write characterisation tests against the current behaviour and confirm green". For anything touching money, state the rounding and the currency basis. For anything touching a gate, state whether it fails OPEN or CLOSED and why.>

### Invariants this order must preserve
<Name the specific §4 invariants this touches and how each stays true. Never write "all of them".>

### Database / migration impact
<"None." or: the exact columns/tables; the RLS policy for any new tenant table IN THE SAME MIGRATION; the backfill rule; whether the downgrade is safe and what it loses; whether a data migration must print a reconciliation report.>

### Testing requirements
<Named test files and named test functions. Include at minimum:
 - one granted-role and one denied-role authorization case,
 - one cross-tenant case asserting 404 (never 403),
 - one financial-correctness case if money is touched,
 - one concurrency/idempotency case if the write can race,
 - one negative case per adversarial category in §8 that applies.>

### Acceptance criteria (verifiable checklist)
- [ ] <Each item is a thing a reviewer can OBSERVE — a status code, a stored value, a test name, a file that exists. Never "works correctly", never "is robust".>

### Rollback strategy
<Code revert? Migration downgrade — is it written AND tested? What is lost on downgrade? Is any effect one-way (revoked sessions, corrected data)? What is the narrow mitigation that does not require a full revert?>

### Documentation to update
<Exact files. If the change contradicts an ADR, say which and how it is reconciled.>

### Self-verification block
```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic upgrade head && alembic check   # if a migration
python -m pytest <the new/changed test files> -q
python -m pytest -q                                                              # full baseline
<a command that DEMONSTRATES the fix — a grep proving the old path is gone, a
 script printing the corrected figure, a parsed XML assertion. Not just "tests pass".>
cd ../frontend && npm run build                                                  # if the SPA changed
```

<!-- ═══════════════ END: WORK-ORDER TEMPLATE ═══════════════ -->

## How to write good acceptance criteria

**Rule:** every criterion must be falsifiable by a person who did not write the code, in under five minutes, without reading the diff.

| Bad | Good |
|---|---|
| "Validation added" | "`POST /vendors` with `iban="DE00000000000000000000"` returns 422 with `code="invalid_iban"` and creates no row" |
| "Permissions enforced" | "`role_client("user_free")` gets 403 on `GET /jobs`; `role_client("admin")` gets 200" |
| "Handles concurrency" | "Two concurrent submissions over an overlapping invoice: exactly one returns 200, the loser returns 409 and its claim status is **unchanged**" |
| "Audit logging works" | "An `AuditEvent` with `action="vendor.update"` exists, its `meta` contains `old`/`new` for each changed field, and no full IBAN appears anywhere in `meta`" |
| "No cross-currency bugs" | "`ap_aging` over EUR+SEK+PLN returns per-currency buckets; `test_fi15_no_aggregate_sums_across_currencies_without_conversion` is green" |
| "Migration is safe" | "`alembic upgrade head && alembic downgrade -1 && alembic upgrade head` is clean; the downgrade refuses while a `pending` change request exists" |

Additional rules:
1. **Quantify the tolerance.** "€0.02 allowed, €0.03 blocked" — never "within tolerance".
2. **Name the actor.** "the requester cannot approve" beats "SoD is enforced".
3. **State the negative.** For every "X is allowed", write the matching "Y is refused, with this code".
4. **Include the unchanged.** Behaviour-preserving refactors need a criterion saying which existing test file passes **unmodified**.
5. **Cap the count.** More than ~12 checkboxes means the order is two orders. Split it.

## How to write good test requirements

1. **Name files and functions.** `tests/test_vendors_authz.py::test_invalid_iban_rejected`, not "add authz tests".
2. **Test through the real path.** HTTP route or service function. A hand-written `select()` in a test proves your test works, not the app.
3. **Overlap the fixtures.** For isolation tests, tenant A and tenant B must carry *identical-looking* data. Distinct data can pass a broken filter by accident.
4. **Every gate gets a both-sides pair** — one input just inside the boundary and one just outside (`SEK 3,999` blocked / `SEK 4,000` allowed; `€399.99` blocked / `€400.00` allowed; `€0.03` blocked / `€0.02` allowed).
5. **Every predicate that must not drift gets a shared-usage test** — inject one bad input and assert *every* consumer of the predicate blocks with the same message set.
6. **A test that cannot fail proves nothing.** Any coverage/parity/scan test ships with a self-test that deliberately seeds a violation and asserts detection.
7. **Concurrency belongs on real Postgres.** SQLite will not reproduce a lost-update race. Put such tests where the `postgres` CI job runs them.
8. **Assert the absence, not only the presence** — no full IBAN in audit meta; no `"9"` emitted by any goods-code mapping; no `Ccy="EUR"` on a foreign amount; zero rows of tenant B.

---
---

# PART D — SPECIALIST REVIEW PROMPTS

Run each in a **fresh session** after a work order is implemented. A reviewer **reports**; it does not fix. Every review ends with an explicit verdict: **APPROVE** · **APPROVE WITH REQUIRED FIXES** · **REJECT**, and REJECT must name the single blocking item first.

---

## D1 — Security Engineer review

<!-- ═══════════════ COPY: SECURITY REVIEW ═══════════════ -->

You are a **security engineer** reviewing a change to `/home/user/Bid_it`, a multi-tenant financial SaaS. Assume hostile authenticated users inside a tenant, and a hostile tenant against another tenant. You report; you do not fix.

Read the diff (`git diff main...HEAD`) and then check:

**Authorization** — does every new/changed route declare a permission structurally (`require_perm` on the router or the route)? Is anything on `PUBLIC_ROUTES`, and does its reason survive scrutiny? Is there any in-handler check that is *weaker* than the declared one? Does any change make a previously-gated route reachable?

**Tenancy** — does every new query filter `org_id`? Does every new tenant-scoped table ship its RLS policy **in the same migration** and appear in the tenant-model registry (`tests/test_rls.py` set-equality)? Does a cross-tenant fetch by id return **404, never 403**? Does any new code path call `get_current_user_unscoped`, and if so is it justified and filtered by `user_id`?

**Secrets & PII** — any secret, real IBAN, real VAT number, real company name or address in code, tests, fixtures or migrations? Does any audit `meta`, log line, error message or exception carry a full IBAN, a token, a password hash or a sealed secret? Is any comparison of a secret non-constant-time?

**Input handling** — is every externally-supplied value validated at the schema boundary *and* the invariant enforced in the service? Any XML parsed without `defusedxml`? Any file written using an attacker-controlled path? Any URL fetched without the SSRF checks (`http(s)` only; no localhost, IP literals, private/loopback/link-local/reserved ranges including `169.254.169.254`), re-checked **at delivery time**?

**Segregation of duties** — where money or bank details move, can one principal complete the whole action alone? Is any admin override silent rather than audited?

**Fail-open vs fail-closed** — for every new gate, is the direction stated in a docstring, and is it the right one? (Scan errors fail *open*; a configured policy that is exceeded fails *closed*; a missing deny-list or missing config fails *closed*.)

**Reject if you find any of:** an unclassified route; a tenant-scoped table without an RLS policy; a full IBAN or secret in a log/audit/error; a cross-tenant 403 that leaks existence; a bank identifier writable without a second approver; a weakened or skipped security test; a `except: pass`.

Output: findings ranked most-severe first, each with file:line, the concrete exploit path ("as a member of tenant A with role X, I can …"), and the minimal fix. Then the verdict.

<!-- ═══════════════ END: SECURITY REVIEW ═══════════════ -->

---

## D2 — QA Automation review

<!-- ═══════════════ COPY: QA REVIEW ═══════════════ -->

You are a **QA automation engineer** reviewing a change to `/home/user/Bid_it`. Your question is not "do the tests pass" — it is **"would these tests have caught the bug this change fixes, and will they catch its regression?"** You report; you do not fix.

Check:

1. **Baseline integrity.** Run `cd backend && . .venv/bin/activate && python -m pytest -q`. Compare the pass count to the number stated in the PR. Any unexplained delta is a finding.
2. **No test was weakened.** `git diff main...HEAD -- backend/tests/` — look for loosened assertions, widened tolerances, changed expected status codes, new `skip`/`xfail`, deleted cases. Each one must be explicitly justified in the PR body; unjustified is a **REJECT**.
3. **Fixture privilege raises are listed.** If a role fixture was raised to make an authorization change pass, is it in the PR body? That list is a required deliverable, not a detail.
4. **The negative cases exist.** For every new capability: a denied-role case, a cross-tenant case asserting 404, a malformed-input case, and — where relevant — over-credit, over-payment, replayed idempotency key, stale version, mixed currency, concurrent writer.
5. **The tests can fail.** For any coverage/parity/scan test, is there a self-test that seeds a violation and asserts detection? Mutate one assertion locally and confirm the suite goes red; if it stays green, the test is decorative — a finding.
6. **The real path is exercised.** Isolation and query tests must call the route or the service, never a hand-written `select()`.
7. **Boundary pairs.** Every threshold has a just-inside and a just-outside case.
8. **Determinism.** No dependence on wall-clock now, on test ordering, on network, or on a shared global. `tests/conftest.py` already isolates storage and rate-limit state per test — did the change respect that, or introduce a new global?
9. **Frontend.** If the SPA changed: `npm run build` clean, and a Playwright happy path in `frontend/e2e/` covers the new flow.
10. **CI parity.** Would this pass the *actual* CI jobs — `ruff check` + `ruff format --check` + **`mypy app`** (whole app, stricter than `make typecheck`), single Alembic head + `alembic check`, the Postgres RLS/concurrency job, the frontend build?

**Reject if:** an assertion was weakened without justification; a new gate has no negative test; a coverage-style test has no self-test; a concurrency claim is tested only on SQLite.

Output: a table of gaps (what is untested, what could regress silently, what is flaky), the exact tests you would add with names, then the verdict.

<!-- ═══════════════ END: QA REVIEW ═══════════════ -->

---

## D3 — Database Architect review

<!-- ═══════════════ COPY: DATABASE REVIEW ═══════════════ -->

You are a **database architect** reviewing schema and migration changes in `/home/user/Bid_it` (Postgres in production, SQLite for the fast test suite, Alembic with 61 revisions and a single head). You report; you do not fix.

Check:

**Migration hygiene** — single head (`alembic heads | wc -l` == 1)? `alembic upgrade head` clean on both SQLite and Postgres? `alembic check` shows no model drift? Is the **downgrade written and actually tested** (`downgrade -1 && upgrade head`)? Does the downgrade destroy data, and is that stated in the docstring? Was any already-shipped migration edited (never acceptable)?

**Tenancy** — does every new tenant-scoped table carry `org_id`, a **composite `(org_id, id)` foreign key** for intra-tenant references, an RLS policy **in the same migration**, and membership of the tenant-model registry so `test_rls.py::test_rls_migration_covers_every_tenant_table` stays exactly equal?

**Constraints do the work, not conventions** — is uniqueness enforced by a constraint or by application code that a second writer can race? Is a state machine's legal set enforced by a `CHECK` or an enum where cheap? Are FKs `RESTRICT` where accidental deletion would lose legal data, and `CASCADE` only where the child is genuinely owned?

**Types** — money is `Numeric(14,2)`, never `Float`/`REAL`. Timestamps are timezone-aware and consistent with the existing base mixin. Enums are stored consistently with the codebase's existing pattern (do not introduce a third style). A quantity that is a denominator (litres) must **not** be quantized to 2dp.

**Indexes** — does every new query pattern have a supporting index, tenant-prefixed (`(org_id, …)`) so it is usable under RLS? Any index that duplicates an existing one? Any unbounded query with no `LIMIT` over a tenant table?

**Data migrations** — does it print a reconciliation report (counts per bucket, rows changed)? Does it ever silently discard or overwrite a customer value? Is it idempotent if re-run? Is it safe to run while the app is live, or does it need a maintenance window (state which)?

**Reject if:** a tenant table has no RLS policy; a downgrade was never executed; money is stored as a float; a data migration overwrites values without a report; a unique business key is enforced only in Python.

Output: findings with the migration file and line, the failure scenario ("with two concurrent writers, …" / "on a table with 5M rows, …"), and the corrected DDL. Then the verdict.

<!-- ═══════════════ END: DATABASE REVIEW ═══════════════ -->

---

## D4 — Performance Engineer review

<!-- ═══════════════ COPY: PERFORMANCE REVIEW ═══════════════ -->

You are a **performance engineer** reviewing a change to `/home/user/Bid_it` (FastAPI + async SQLAlchemy + Postgres). The system is a modular monolith serving many small tenants; correctness has priority over speed, but a per-request regression multiplies across every tenant. You report; you do not fix.

Check:

1. **Query count per request.** Did the change add a query to a hot path (`get_current_user` runs on **every** authenticated request)? Instrument with a SQLAlchemy `before_cursor_execute` listener in a test and count. A per-request addition greater than 1 needs a justification or caching.
2. **N+1.** Any loop issuing a query? Any missing `selectinload`/`joinedload` on a relationship the response serialises? The document-presence check pattern in the plan is explicit: **one-query set membership, never N+1**.
3. **Unbounded results.** Any tenant-table query with no `LIMIT` and no pagination? (`vendors.list_vendors` caps at 1000 — follow that pattern.) Any `.all()` over a fact table?
4. **Aggregation location.** Is the aggregation done **in the database** (the Explore engine's design) or pulled into Python? Pulling a line-item fact grain into Python is a defect.
5. **Async discipline.** Any blocking call (`requests`, `time.sleep`, sync file I/O, a CPU-bound parse) inside a request coroutine? Parsing, OCR and the monthly close belong on the worker tier via the jobs queue, **never inline in a web request**.
6. **Indexes match the new access pattern**, tenant-prefixed.
7. **Job queue.** Is a new job idempotent, date-keyed where periodic, and safe to run concurrently across workers? Does a failure degrade gracefully rather than raising into the scheduler?
8. **Payload size.** Does a response now embed a large collection that used to be paginated?
9. **Known accepted limits** — do not re-litigate these unless the change makes them worse: per-process rate limiting (N replicas = N × limit), no materialised analytics rollups yet, single-region residency. Act on a metric, not a fear.

**Reject if:** a query was added to `get_current_user` without reusing an existing fetch; an N+1 was introduced on a list endpoint; a blocking or CPU-bound call landed in a request path; an unbounded query over a tenant fact table was added.

Output: findings ordered by expected impact, each with the measurement you would take (query count, row estimate, latency) and the fix. Then the verdict.

<!-- ═══════════════ END: PERFORMANCE REVIEW ═══════════════ -->

---

## D5 — FinTech / Accounting correctness review

<!-- ═══════════════ COPY: FINTECH REVIEW ═══════════════ -->

You are a **financial-systems correctness reviewer** (controller's eye) for `/home/user/Bid_it`. Your standard is: **would an auditor accept this, and could a customer be paid the wrong amount?** You report; you do not fix.

Check every one that applies:

- **FI-1 Decimal only.** No float anywhere in a money path — including intermediate arithmetic, JSON parsing and test fixtures. Rounding is `ROUND_HALF_UP` via `app/core/money.py::q2`. Confirm `tests/test_money_invariants.py::test_money_never_uses_float` still covers the new code.
- **FI-2 Server recomputes.** No client-supplied total is trusted, anywhere.
- **FI-3 Ledger equals cache.** `SUM(ledger) == cached amount_paid` on both AR and AP.
- **FI-4 Derived status.** Payment/aging status is computed, never stored. `overdue` beats `partial` in precedence.
- **FI-5 No overpayment.** AR capped at `total − credited`; AP capped at `total`; an allocation capped by both the receipt's unallocated balance and the invoice's outstanding — **enforced under a row lock**, not by a read-then-write.
- **FI-6 No over-crediting.** 1-cent tolerance; `credited_total` clamped.
- **FI-7 Gap-free numbering** per issuer entity, proven under **real Postgres** concurrency.
- **FI-8 Immutability.** An issued document never changes; correction is a credit note whose effect is derived.
- **FI-9 Rendering.** PDF and XML are rebuilt from stored lines through the same tax function — they cannot disagree.
- **FI-10 / FI-15 Currency.** No report sums across currencies; no aggregate converts without a **recorded** rate; no file or export labels a foreign amount EUR.
- **FI-11 FX provenance** ∈ `{eur, stated, ecb, unknown}`; `unknown` → `NULL`, never a guess. Conversion **divides** (ECB publishes units per 1 EUR). The rate is the most recent **on or before** the transaction date.
- **FI-12 / FI-13 Idempotency.** Recurring generation and invoice email are idempotent across workers; a replay is a no-op, not a duplicate.
- **FI-14 Export safety.** Every CSV/Excel cell of free text is formula-injection-safe (a leading `=`, `+`, `-`, `@` is neutralised).
- **FI-16 Fee freezing** (transport). A claim's fee rate and minimum are frozen at submission; only the **base** changes (claimed → paid); the fee is charged on the **paid** amount over exactly the locked set — never a period `SUM`.
- **Append-only ledgers.** A reversal is a negative entry; nothing is deleted or updated in place.
- **Audit.** Every amount-affecting mutation is audited in the same transaction, with old→new.
- **Boundary arithmetic.** Thresholds are decided on the `Decimal` form: `q2("399.994") < 400`, `q2("399.995") >= 400`, `f2(2.675) == 2.68`. A total sitting exactly on a legal threshold must never flip on binary-float noise.

**Reject if:** a float touches money; a total is trusted from the client; an overpayment or over-credit path exists without a row lock; a foreign amount is labelled EUR; a ledger row is updated or deleted rather than reversed; an amount-affecting mutation is unaudited; a frozen figure can be re-derived after freezing.

Output: findings with the exact wrong-money scenario ("a €1,000 SEK invoice paid on a Sunday would produce …"), the affected records, and the fix. Then the verdict.

<!-- ═══════════════ END: FINTECH REVIEW ═══════════════ -->

---

## D6 — UX review

<!-- ═══════════════ COPY: UX REVIEW ═══════════════ -->

You are a **product designer / UX reviewer** for `/home/user/Bid_it`'s React SPA (`frontend/src/`). The user is a finance person under time pressure who will be blamed for a wrong number. You report; you do not fix.

Check:

1. **States.** Does every new screen have **loading, empty and error** states using the existing primitives (`QueryState`, `Skeleton`, `EmptyState`, `ErrorState`)? An empty state must say what to do next, not just "no data".
2. **Destructive and irreversible actions** go through `ConfirmDialog` and say **what will happen and to whom** — "the bank will see a new message id", "these 3 suppliers will NOT be paid".
3. **Honest labels.** Nothing implies a capability the backend does not have. Named examples to police: "cash position" is a **working-capital gap (receivables − payables), not a bank balance**; "cash flow" is **historical**, not a forecast; excise and estimate figures are **indicative and assert no eligibility**; a peer benchmark below the minimum cohort renders "cohort too small", never a number.
4. **Permission-aware rendering** — actions the user cannot perform are hidden or disabled with a reason. **And it is cosmetic only**: never present it as a security boundary, and never assume the server will not re-check.
5. **Errors are actionable.** The `{"detail","code"}` envelope reaches the user as a sentence they can act on, plus the `X-Request-ID` for support. Never a raw code, never a stack trace, never a silent failure.
6. **Money presentation.** Currency always shown; the basis stated where it is not obvious (VAT-inclusive vs net; NET EUR/L for fuel prices); never a mixed-currency total presented as one number.
7. **Provenance visible where it matters.** In capture review: which fields were extracted vs defaulted vs missing, the confidence, and a visually distinct low-confidence flag (threshold 0.75). The user must be able to see *why* a value is there.
8. **Consistency.** Reuses `src/components/ui/*`; matches `docs/DESIGN_SYSTEM.md`; no new UI library; no bespoke table when `DataTable` exists.
9. **Flow completeness.** Can the user finish the job end to end without leaving for the API docs? For AP: upload → poll → review → confirm → approve → schedule → pay. For AR: create → issue → PDF/XML → send → track → credit note → apply cash.
10. **Accessibility basics.** Keyboard reachable, labelled inputs, focus visible, colour not the sole signal for an error or a status.

**Reject if:** a screen has no error state; a destructive action has no confirmation; a label overstates what the system knows; a number is shown without its currency or basis; an error surfaces as a raw code or vanishes.

Output: findings by screen with a screenshot-level description of what the user sees versus what they need, then the verdict.

<!-- ═══════════════ END: UX REVIEW ═══════════════ -->

---
---

# PART E — VAT/TRANSPORT HARVEST PROMPT

> Prepend **Part A** *and* this prompt to every Epic-G work order (G0.x, G1.x, G2.x, G3.x, G4.x). It is the standing context for the transport vertical.

<!-- ═══════════════ COPY FROM HERE: VAT/TRANSPORT HARVEST ═══════════════ -->

You are porting the **EU cross-border VAT refund domain** into `/home/user/Bid_it` as a plug-in bounded context. The source of the *rules* is a retired system, Fleet Fuel, analysed as 76 numbered requirements **R1–R76** in `docs/plan/BA_fleet_fuel.md`. **That document is the sole authoritative source — the Fleet Fuel repository has been (or is being) deleted per Part F and must not be assumed to exist.** You are porting a **specification**, not a codebase.

## E.0 — PII QUARANTINE (read before anything else)

The retired Fleet Fuel codebase contained **real client personal and commercial data as module constants** — real company names, real EU VAT numbers, real addresses, real bank references, real invoice numbers (e.g. in `customer_master.CUSTOMERS`, `BANKS`, `SUPPLIER_ACCOUNTS`, `supplier_master.SUPPLIERS`/`VAT_REGS`/`INVOICE_REG`, `vat_config.INVOICES`/`ISSUERS`) — and three databases (`customers.db`, `fuel_history.db`, `suppliers.db`) are **committed to its git history**.

**Absolute rules:**
1. **Never copy any value** out of Fleet Fuel into `/home/user/Bid_it` — not into code, tests, fixtures, docs, comments, commit messages or scratch files. Git history is permanent; a reverted commit does not undo the exposure.
2. **Never copy code.** Read Fleet Fuel for *rules and reasoning*. The stacks differ (Flask + procedural + ~26 SQLite databases vs FastAPI + async SQLAlchemy + one Postgres); a copied line is both a design regression and a provenance question.
3. **All fixtures are synthetic** — generated by `backend/tests/factories/transport.py`, realistic in *shape* (correct VAT prefix and length, MOD-97-valid IBANs, plausible invoice-number formats) and **fictional in content**.
4. The CI PII scan (`scripts/pii_scan.py`, WO-6) is a **required check**. Never disable it. A false positive is resolved by an allow-list entry with a named verifier, never by turning the job off.
5. If you are unsure whether a value is real, **treat it as real.**

## E.1 — The harvest protocol (binding on every Epic-G PR)

Every harvested rule arrives as **three artifacts**:

1. **A typed model or a pure function** in `app/models/transport/` or `app/services/transport/`.
2. **A test** named `test_r{n}_{slug}` under `backend/tests/transport/`, whose **docstring cites the R-number and the legal source** — e.g.
   `"""R9 — Art. 15 Dir. 2008/9/EC; CJEU C-294/11 Elsacom: 30 Sep year+1 is a fatal time-bar."""`
3. **A row in `docs/transport/rules.md`** mapping R-number → module → test → legal source.

**A G-task PR without its R-test does not merge.** This is the mitigation for risk T-2: 2,422 Fleet Fuel tests are being discarded, and losing one gate either forfeits a client's money or files an invalid claim.

## E.2 — The seam (ADR-P3 / ADR-0023) — six binding rules

```
app/models/transport/      fuel_transaction.py, vat_claim.py, supplier_registration.py,
                           excise.py, overcharge.py, contract_term.py
app/services/transport/    capture.py, claim.py, claim_gates.py, goods_codes.py,
                           entity_resolution.py, excise.py, overcharge.py, fuel_analytics.py
app/api/routes/transport/  fuel.py, claims.py, recovery.py, excise.py, overcharges.py
backend/tests/transport/   one file per R-requirement group
```

1. **Transport owns only transport tables.** It never adds a column to `invoices`, `line_items` or `vendors`. Fuel detail lives in `fuel_transactions` with a **nullable** FK to the AP invoice it was captured from.
2. **Transport reads the core through services, never through joins** — `invoice_service`, `vendor_service`, `documents`, `fx`, `vat`. Extend `backend/tests/test_boundaries.py` with an assertion that **no module under `app/services/transport/` imports a model from another domain package**.
3. **Transport is an entitlement.** A new `org_modules` key `transport`, **default off**, plan-gated exactly like `issuing`/`expenses`. A tenant without it gets 403 from `modules.require_enabled` on every transport route and pays zero query cost.
4. **Transport reuses the platform floor unchanged** — `core/money`, the hash-chained audit, tenancy (`org_id` + composite FK + RLS), the jobs queue for the close, `filesec` at the single upload choke point, `documents` for the vault, `keyvault` for any stored credential.
5. **Transport adds permissions, not roles.** New `Permission` members `VAT_READ`, `VAT_WRITE`, `VAT_SUBMIT`, `TRANSPORT_READ` join the existing 20 in `app/core/authz.py` and get rows in **all 8** `ROLE_PERMISSIONS` entries. No new role tier. Update `docs/security/authorization-policy-matrix.md` in the same PR so `test_every_role_is_in_the_matrix` stays green.
6. **Transport never gates a core figure and the core never gates a claim.** Excise, overcharge, benchmark and any AI seam are **advisory** and cannot mutate a legal figure.

**The one translated invariant.** Fleet Fuel kept the claim data in a physically separate SQLite database because a monthly close does `DELETE by period + INSERT`, and claim lines were derived *live* from transactions at read time. Do **not** copy that into Postgres. Instead — and this is strictly stronger:
- a claim **materialises and freezes `vat_claim_lines` at submission**, alongside the already-frozen `vat_eur`/`vat_local`/`fee_pct`/`fee_min`/`fee_eur`;
- nothing reads through to `fuel_transactions` after freezing, so a re-close cannot change what was filed;
- `fuel_transactions` rows locked into a submitted claim are protected **at the database level** — the period-scoped delete excludes rows referenced by a lock row, and a **`RESTRICT` FK** from `vat_claimed_invoices` makes accidental deletion an error;
- the close runs as a **durable job** (`jobs` kind `transport.close`), tenant-scoped, **idempotent by `(org, period)`**, restartable, halting on first failure, with one audit trail.

## E.3 — The case-law-derived rules that MUST NOT be lost

Each is a rule where getting it wrong costs a client real, unrecoverable money. Each needs its own test.

**1. The 30-September time-bar is fatal (R9 — Art. 15; CJEU C-294/11 *Elsacom*).**
A refund application for calendar year *Y* must be submitted by **30 September of year Y+1**. The deadline is a **preclusive time-bar**, not a procedural nicety: miss it and the right is **permanently extinguished**. Implement a 60-day risk window (`DEADLINE_RISK_DAYS = 60`) scanning **both `{today.year, today.year-1}`** — a tight, exhaustive bound. North-star KPI: **deadline misses = 0**.
*Test:* a 2025-Q4 claim shows at-risk from 2 Aug 2026 and OVERDUE from 1 Oct 2026.

**2. National-currency minimums are FIXED statutory amounts, never an FX conversion (R8 — Art. 17).**
The EUR base is **€400** (period ≥ 3 months and < a calendar year) and **€50** (calendar year or the remainder of a year). Member States outside the euro publish **fixed national amounts** which are compared against `vat_local` — **`SEK 4000 / 500`** and **`DKK 3000 / 400`**. These are statutory figures set in national law; **converting €400 at today's ECB rate is wrong** and will block or allow the wrong claims. Euro countries **and Poland** are intentionally absent from the national table and fall back to the EUR base. An admin override is allowed and **must be recorded in `status_note`**.
*Why the gate exists at all:* a below-minimum claim would be **refused** *and* its invoices would be locked out of the annual mop-up — the money is permanently lost.
*Test:* `SEK 3,999` blocked / `SEK 4,000` allowed; `€399.99` blocked / `€400.00` allowed — decided on `Decimal`, never float.

**3. Goods code default is "10", NEVER "9" (R11 — Art. 9; Reg. 1174/2009 & 79/2012 Annex III).**
Fuel → **`"1"`**; road tolls → **`"4"`**; AdBlue, parking, service and anything unrecognised → **`"10"` (Other)**. Code **`"9"` is "luxuries, amusements and entertainment"** — the archetypal non-deductible category. Filing an operating fluid under 9 invites refusal of the whole claim. *(Historical note from the source system: tolls were once coded 3 and AdBlue/parking 9 — both were bugs. Do not regress.)*
*Test:* the unknown default is `"10"`, and **no mapping anywhere emits `"9"`** — assert over the entire mapping table, not one case.

**4. A `2B` document request is a SOFT reminder, never a forfeiture gate (R12 — CJEU C-133/18 *Sea Chefs*).**
When the refunding state requests additional information, the one-month response period is **not preclusive**: the applicant may still supply the information later, including before the national court. Therefore `2B` carries an `action_deadline`, appears on a worklist, and **changes no status and blocks nothing** when it passes. **Never harden it into an auto-reject.**
*Test:* passing the `action_deadline` changes no status and blocks nothing.

**5. One shared `is_synthetic()` predicate across all four gates (R3).**
```
is_synthetic(ref, vat_id) ==
    "INPUT" in ref or ref.startswith("ALL:") or ref == "UNMATCHED" or "INPUT" in str(vat_id)
```
Used by **the lock gate, the checklist gate, the readiness check and the workbook builder** — deliberately the same function, "so they all block the same set". **A pack with any synthetic line cannot be filed.**
*Test:* inject **one** synthetic line and assert **all four** surfaces block with the same message set. A drift between them is the bug this rule exists to prevent.

**6. One invoice, one submission — locks released only by withdraw (R4, R5).**
`vat_claimed_invoices` with `UNIQUE(org, entity, refund_country, supplier, invoice_ref)`. The lock is acquired **in the same transaction as the status change**, via a **plain `INSERT` (never an upsert)** so a lost race **raises and aborts the whole transition** rather than proceeding as if the lock were won. **Only an explicit `withdraw_claim` releases a lock** — rejection (`3B`), confiscation (`3C`) and appeal (`3D`) **keep** them, so a contested invoice cannot be re-claimed elsewhere and create a duplicate submission.
*Test (real Postgres, in the existing `postgres` CI job):* two concurrent submissions over an overlapping invoice — exactly one succeeds and **the loser's status is unchanged**. After `3B` the invoice cannot be claimed in another period; after withdraw it can.

**7. Capture reads the SELLER legal entity printed on the invoice (R20, R21, R22).**
Never the buyer, never a factoring entity. Eurowag: read the per-country seller from the invoice footer (`Pārdevējs / Verkoper: <name…legal form>, <address>, PVN reg. Nr.: <VAT>`) to get the **local issuing entity per country** (BE BVBA, LT UAB, `a.s.`, …) — **not** the Czech "W.A.G. Issuing Services, a.s." factoring entity the receivables are ceded to; filing that would be wrong on both name and VAT prefix. E100: **anchor** the seller name and VAT to the `"E100 International Trade"` marker itself, because a generic seller/buyer heuristic grabs the **buyer's** VAT id where it repeats on annexe pages.
Matching is **marker-only** — admin-curated, country-scoped brand/VAT registrations, **no fuzzy auto-pairing**. The UI **leads with the legal entity**; the supplier code is a confirm-able suggestion.
**Per-country learning:** confirming a statement whose supply country ≠ the supplier's home country seeds **that country's** registration (`source='capture'`, **never clobbering a curated value**) and **does not** change the group primary legal name / home VAT, nor queue a spurious home-VAT change.
*Test:* a Eurowag BE invoice yields the BE entity, the CZ primary is untouched, and **no** pending change is queued.

**8. IBAN, VAT number and registration number are NEVER auto-updated on an existing supplier (R23).**
Regardless of AI verification, regardless of confidence. They become **pending change requests** requiring explicit approval by a different person, showing the legal entity and a link to the source PDF. Safe fields (legal name, address, phone, email) may auto-update when verified or high-confidence. A **brand-new** supplier may be created with captured IBAN/VAT but lands **provisional**. This is the same control WO-2 builds for `vendors` — **reuse it, do not build a second one.**
*Test:* feed a capture with a changed IBAN → the stored IBAN is unchanged and a pending request exists.

## E.4 — Additional non-negotiables from the R-set

- **R1 grain:** a claim is keyed `(org, entity, refund_country, ref_period)` with period ∈ `{YYYY-Qn, YYYY-YEAR}` (Art. 16: min 3 months, max 1 year, shorter only as the remainder of the year). Same key → upsert, never a duplicate.
- **R2 line grain:** one row per `(invoice, product code)`; **never an `ALL:` aggregate**; unresolved → `UNMATCHED` (a hard block, not an invented aggregate).
- **R6 annual = mop-up:** a `-YEAR` claim **excludes** invoices already locked to a quarter (skip, not a conflict); a **quarterly** claim treats any overlap as a **duplicate and blocks**; an annual claim with an empty set is refused.
- **R7 hard period-end gate:** `today > period_end_date(period)` (Q2 → 30 Jun; YEAR → 31 Dec).
- **R10 document presence:** every invoice in the claim set needs ≥1 vaulted document — a **one-query set membership**, never N+1.
- **R13 fee freezing:** freeze `fee_pct`, `fee_min`, `fee_eur` and the VAT base at first entry to a locking state, computed over **exactly the locked claim set** (never a period `SUM(vat_eur)`). On `paid`, recompute `fee_eur = compute_fee(paid_amount or vat_eur, frozen pct, frozen min)` — **only the base changes; the rate and minimum are never re-derived.** `% takes priority; if it falls below the per-declaration minimum, the minimum is charged`, returning `(fee, basis ∈ {percent, minimum})`. `record_payment` stamps `paid_amount` and drives the claim to `3A` **in one transaction**.
- **R15 receipt-control waivers:** permitted **only** where the ref is synthetic **AND** starts with `INPUT` **AND** the supplier has **no registered invoice at all** for that refund country. Waiving a supplier that *has* invoices is **refused** — "an UNMATCHED transaction there is a note-matching fix, not a missing invoice; waiving would drop claimable VAT." A waived supplier is excluded **by construction** and the waiver is stamped into `status_note` at submission.
- **R16 note→invoice overrides** change **only the association, never an amount**, and are validated **twice** — at set time (refuse a non-registered or synthetic target) and at read time (re-validate against the live registered set, silently dropping a stale one).
- **R17 lifecycle:** `1A/1B/1C/1E` are **AUTO, system-derived, never user-settable**. `derive_stage()`: any non-period checklist item failing → `1A`; period not ended → `1B`; a caveat in the verdict → `1C`; else `1E`. **The only legal first manual step from an unlocked claim is `2` (Submit).**
- **R44 customer lifecycle** `prospect → pending → active → inactive`; **every legal/claim gate keys off `active`**; `add_prospect` is idempotent on company name and **never downgrades a real client**.
- **R45 checklist as data, not code:** key, label, scope (`customer` once / `country` per refund country), check type (`document` / `data`), reference, active, sort. Defaults: contract, customer data, bank account, **NACE** (required — Art. 11 requires the business-activity description via harmonised NACE codes), trade register (customer scope); power of attorney (country scope). `_field_ok` treats **any value containing `"INPUT"` as missing**. **Document expiry re-blocks** — an expired PoA drops the claim to `1A`. An open PoA document request enriches the item's **label** only; the boolean `ok` is untouched.
- **R49/R50 price basis:** **NET EUR/L, final (VAT excluded, rebates applied)** stated on **every** price surface. Effective price = `net_eur_eff / qty`. Expose **both** as-invoiced (`eur_l_doc`) and effective (`eur_l_eff`). **Guard the input source**: feeding an unadjusted file must **fail or warn loudly**, never silently produce list-price analytics. `qty` is **deliberately not money-quantized** — it is the €/L denominator.
- **R51 one canonical query layer:** every report, export, dashboard and materialised metric derives from one registry. Materialised metrics carry a **drift check that recomputes through the same code path**; an un-materialised period still renders via a live fallback.
- **R52/R53 two overpay definitions, labelled by grain,** and they **will not reconcile — that is correct**. Legal framing must not be flattened: contract breach = "money the supplier owes"; same-day overpay = **"negotiation evidence, NOT a contractual claim-back"** printed on every sheet; peer/excise/estimate = "indicative, verify".
- **R54 anomaly detection uses no absolute price thresholds** — bounds learned from the data's own spread (2σ, robust modified-z cutoff 3.5) with volume floors (200 L station, 100 L vehicle). *Test:* double every price → the same rows flag.
- **R55 peer benchmark is the antitrust gate:** equal-weight median of the *other* entities (itself excluded, deliberately not volume-weighted), suppressed below `PEER_MIN_CONTRIBUTORS = 2`, and **restricted intra-tenant** — a client never sees another client's prices.
- **R42 excise:** per `(entity × country)`, `litres × rate / 1,000 L`, seven countries (BE·FR·IT·SI·HU·ES·HR). Rates are **indicative, admin-overridable defaults** and the figure **asserts no eligibility** (vehicle ≥ 7.5 t / carrier registration are not modelled). Caveats surfaced loudly on **every** surface showing the number.
- **R43 refund-estimate funnel:** in-memory parse, **no product-DB write**, per-country aggregation with the minimum-threshold flag, and an explicit "**a sales preview, never a filed figure**" caveat.
- **R39 client portal:** plain-language stages only (**prep → ready → filed → awaiting → refunded**, plus "needs attention"). **No internal codes, no actions, no fees** to a client-role session.
- **R25 two independent validation regimes** — an **engine tie-out** to the invoice document (`lines` tolerance **0 — exact count, always**; `gross_local` 0.02–0.05; `net_eur` 0.05; failure **halts the close**, but only after processing every supplier so all failures are visible at once) **and** a **capture review gate** (verdict lattice `ok < warn < error`; `can_commit = (errors == 0) and (tie is None or tie.ok)`; **warnings never block**; batch tie-out `abs(q2(Σ net+vat) − q2(coversheet_total)) <= 0.02` **compared on Decimals**).
- **R26 post-capture checks are advisory:** IBAN MOD-97 (error), VAT-ID **structural only** (warn — a **live VIES lookup is never done inline**; offline-graceful, returns "not checked", never raises or blocks), duplicate across **all** entities (error) / in-batch repeat (warn). **Unknown or uncheckable input yields no finding — fail toward not crying wolf.**

## E.5 — What we deliberately do NOT harvest

Do not port, and do not propose porting: `app.py` (21k lines of hand-concatenated HTML), the ~26 runtime SQLite databases, the nominal `db.py` abstraction, positional row lists, the overloaded `note` column (**split it** into `invoice_ref` + `provenance_note` + typed flags — most of `_resolve_inv`'s complexity and the whole `note_invoice_overrides` table exist to compensate for that one choice), `invoicing.py` (Bid_it's AR engine is better and built), `workflow.py` + its visual builder, the DMS/sharing suite, `portal_scraper.py` (**zero real adapters ever fetched a real invoice; real ToS and credential-sharing exposure**), `autopilot.py` (auto-files with no human review — an *absent* verification result still passes), `finance.py`, `bank_recon.py`, `translations_lv.py`, `month_config.py`, and all hard-coded client data.

## E.6 — Definition of done for any Epic-G unit

- [ ] Every rule implemented has its `test_r{n}_{slug}` test with the R-number **and** the legal citation in the docstring.
- [ ] `docs/transport/rules.md` updated with the R → module → test → source row.
- [ ] Zero values copied from Fleet Fuel; `python scripts/pii_scan.py --tree` exits 0.
- [ ] All fixtures come from `tests/factories/transport.py` and are synthetic.
- [ ] `tests/test_boundaries.py` passes, including the transport cross-domain-import assertion.
- [ ] Every new transport table is tenant-scoped, has a composite `(org_id, id)` FK where it references a sibling, ships its **RLS policy in the same migration**, and keeps `test_rls.py` set-equality green.
- [ ] A tenant without the `transport` module entitlement gets **403** on every transport route.
- [ ] Money is `Decimal` ROUND_HALF_UP; `qty` is **not** quantized; every price surface states the **NET EUR/L, VAT-excluded, rebates-applied** basis.
- [ ] Concurrency claims (locks, close-vs-submit) are tested on **real Postgres**, not SQLite.
- [ ] Full backend suite green; `mypy app` clean; single Alembic head; `alembic check` clean.

<!-- ═══════════════ END: VAT/TRANSPORT HARVEST ═══════════════ -->

---

## Appendix — verified repo facts these prompts rely on

| Fact | Where verified |
|---|---|
| 38 route modules, 75 services, 46 models, 43 schemas, 17 core modules | `backend/app/**` listing |
| 115 test modules, ~737 test functions (plan cites ~761 collected) | `backend/tests/` |
| 61 Alembic revisions, single head | `backend/alembic/versions/` |
| 5 CI jobs: lint (`ruff` + **`mypy app`**), backend (single head + `alembic check` + pytest), postgres (NOSUPERUSER role + `test_rls.py` + `test_numbering_concurrency.py`), frontend (`npm run build`), release | `.github/workflows/ci.yml` |
| `make typecheck` runs `mypy app/core` — **narrower than CI's `mypy app`** | `Makefile` |
| `authz.Permission` has 20 members; `Role` has 8; `ROLE_PERMISSIONS` deny-by-default; `require()` raises 403 | `backend/app/core/authz.py` |
| Legacy stored roles `owner/admin/user/user_free` → `OWNER/ADMINISTRATOR/EMPLOYEE/READ_ONLY` | `authz._LEGACY_ROLE`, `app/models/user.py::UserRole` |
| `get_current_user` never checks `Organization.status` | `backend/app/api/deps.py` |
| `vendors.py` create/update: no `authz.require`, no `audit.record`, no version guard, sets `iban`/`bic` | `backend/app/api/routes/vendors.py` |
| `partners.py::_guard` checks only `modules.require_enabled(..., "issuing")` | `backend/app/api/routes/partners.py` |
| `_reconcile` lives in the route module | `backend/app/api/routes/invoice_review.py` |
| `msg_id=f"RUN-{run.id[:8]}"`; `payment_run_sepa` returns `(xml, skipped)` | `backend/app/services/sepa.py:133`, `:105–141` |
| `GET /{run_id}/export` and `/{run_id}/sepa` carry no `PAYMENT_WRITE` | `backend/app/api/routes/payment_runs.py:191,207` |
| `inbound_email_secret` optional; checked only if set | `app/core/config.py:83`, `app/api/routes/email.py:46` |
| `Settings._validate_production` collects `problems` and raises | `app/core/config.py:264` |
| `audit.record(db, action, *, target_type, target_id, meta, org_id, actor)` — never raises, caller commits | `backend/app/services/audit.py:131` |
| `money.q(value, exp)` / `money.q2(value)` ROUND_HALF_UP | `backend/app/core/money.py` |
| `scheduler.DAILY_KINDS` + `enqueue_daily(db, today=)` keyed `f"{kind}:{date}"` per org | `backend/app/services/scheduler.py` |
| `conftest.py` provides `client`, `auth_client` (**owner only**), `db_session`, `parse_upload`; no low-privilege fixture exists yet | `backend/tests/conftest.py` |
| Boundary tests: models/core/services import rules by AST | `backend/tests/test_boundaries.py` |
| Frontend has `Review.tsx` (validation queue) and `ReviewInvoice.tsx` (approval detail) — **not** an extraction/provenance capture-review screen | `frontend/src/pages/`, grep for `extraction`/`confidence` |

---

# PART F — FLEET FUEL DECOMMISSION (RUN ONCE)

> One-time runbook prompt. Execute AFTER WO-6 Step 1 (deny-list) has been built and AFTER
> `docs/plan/*` is committed and pushed to Bid_it. Human-in-the-loop: Phase 3 is irreversible
> and only the repository owner can perform it.

<!-- ═══════════════ COPY FROM HERE: DECOMMISSION ═══════════════ -->

You are decommissioning the retired Fleet Fuel system. The decision is made: the
`kristapsgoncoronoks-ship-it/fleet_fuel_system` repository will be **deleted**, and all future
development happens in Bid_it. Your job is to make that deletion safe, verifiable, and compliant.

## F.0 — Why this is delicate

1. The repo contains **real client PII and business records** (client masters, VAT registrations,
   bank references, committed SQLite databases with transaction history). Deleting it from GitHub
   is a privacy *improvement* — but those business records may be subject to **statutory retention**
   (EU VAT records: 5–10 years depending on member state). Deletion must not destroy the only copy.
2. Git deletion is **effectively irreversible**: GitHub support can restore a deleted private repo
   for a limited window (~90 days); after that, only your own archive exists.
3. The specification harvested from it (`docs/plan/BA_fleet_fuel.md`, R1–R76) is now the sole
   engineering source. The deletion must not orphan any reference to the old repo.

## F.1 — Preconditions (verify, do not assume)

- [ ] `docs/plan/BA_fleet_fuel.md`, `BA_bidit.md`, `ARCH_plan.md`, `PROMPTS.md` are committed AND
      pushed on Bid_it `main` (or an open PR). Run: `git -C /home/user/Bid_it log --oneline -3 -- docs/plan/`.
- [ ] WO-6 Step 1 (salted-hash deny-list) is built, or a documented decision to rely on structural
      patterns only exists in `docs/transport/harvest-protocol.md`.
- [ ] Fleet Fuel `main` (trunk merge commit `5075e08`) is pushed — nothing unmerged on any branch:
      `git -C <fleet_clone> for-each-ref refs/remotes --format='%(refname:short) %(objectname:short)'`
      and confirm every branch tip is an ancestor of `main`.
- [ ] Confirm with the OWNER that no deployed instance of the Flask app serves live clients from
      this repo's clones — **deleting the GitHub repo does not stop a running deployment**, and any
      live VAT-claim operation (30-Sep filing deadline!) must have an owner-approved continuity plan.
      This runbook does NOT decide that; it only refuses to proceed without an explicit answer.

## F.2 — Owner-held archive (before deletion)

From an up-to-date clone:

```bash
git -C <fleet_clone> fetch --all --tags
git -C <fleet_clone> bundle create fleet_fuel_FULL_$(date +%Y%m%d).bundle --all
git bundle verify fleet_fuel_FULL_*.bundle          # must print "is okay"
zip -r fleet_fuel_worktree_$(date +%Y%m%d).zip <fleet_clone> -x '*.git*'
sha256sum fleet_fuel_FULL_*.bundle fleet_fuel_worktree_*.zip > fleet_fuel_archive.SHA256
```

Hand all three files to the business owner for **offline, access-controlled storage** (encrypted
disk / vault). **Never** push the bundle or zip to any git host, cloud drive shared link, or the
Bid_it repo — it contains the PII the quarantine exists to contain. Record WHERE it is stored and
WHO holds it in the owner's records (not in the repo).

## F.3 — Deletion (owner performs)

1. GitHub → `fleet_fuel_system` → Settings → General → Danger Zone → **Delete this repository**
   (type the full name to confirm). Owner-only; 2FA prompt expected.
2. Remove the repo from every automation that references it: CI secrets/deploy keys, Claude
   session sources, scheduled triggers, local `git remote` entries.
3. Delete all working clones EXCEPT the archive from F.2 (`rm -rf <fleet_clone>` on each machine).

## F.4 — Post-deletion verification

- [ ] The repo 404s on GitHub and no longer appears in the session's repo list.
- [ ] `grep -RIn "fleet_fuel_system" /home/user/Bid_it --include='*.yml' --include='*.yaml' --include='*.json' --include='*.toml'`
      returns only documentation references (docs/plan, this file) — no CI/config/code reference.
- [ ] Bid_it CI is green; the PII scan job (WO-6) is a required check.
- [ ] `docs/transport/harvest-protocol.md` records: archive created (date, SHA-256s), repo deleted
      (date), deny-list status, and that `docs/plan/BA_fleet_fuel.md` is the sole surviving spec.

## F.5 — Rollback

Within GitHub's support window (~90 days): contact GitHub support to restore the deleted repo.
After that: restore from the owner's bundle (`git clone fleet_fuel_FULL_<date>.bundle restored/`).
If neither exists, the code is gone — the specification in `docs/plan/BA_fleet_fuel.md` and the
2,422-test behavioural knowledge it encodes are what remain. This is accepted by the decision.

<!-- ═══════════════ COPY TO HERE ═══════════════ -->
