# TODO — Task Board

Live board per the founder's charter (statuses: `Backlog` · `Planned` · `In Progress` · `Blocked` ·
`Testing` · `Review` · `Completed`). Supersedes the 2026-07-27 audit snapshot preserved at the bottom
of this file — every P0/P1/P2 item it listed as `Approved`/`Backlog` is now closed. This file is the
current source of truth; `docs/M0-exit-gate.md` and `docs/plan/plan-a/wo/` (WO-1…47) are the detailed
per-order record.

**Plan:** Plan A (evolve Bid_it) — decided 2026-07-25, reaffirmed 2026-07-28. See
`docs/plan/PLAN_A_vs_PLAN_B.md`. Plan B (`docs/plan/plan-b/GREENFIELD_plan.md`) was considered and set
aside; not executed.

---

## Milestone status

| Milestone | Theme | Status |
|---|---|---|
| **M0** | Security/correctness debt sprint | ✅ **Completed** — WO-1…11 (incl. B1.5). All 12 exit-gate criteria met. See `docs/M0-exit-gate.md`. |
| **M1** | Feature completion + independent audit | ✅ **Completed** — WO-12…46. Every named epic shipped; 18-item audit (R1–R19) closed except two decision-gated/backlog items (below). |
| **M2** | "We can take money" — billing go-live | 🔶 **In Progress** — WO-47 (quota model) + WO-48 (dogfood billing fallback) shipped. Three items still owner-blocked (below). |
| **M3** | Transport vertical phase 1 — VAT refund claim engine | 🔶 **In Progress** — WO-49 (foundation: claim grain, `is_synthetic()`, module entitlement) + WO-50 (`fuel_transactions`: typed model, idempotent ingestion, `product_group` derivation) + WO-51 (`vat_claimed_invoices`: the one-invoice-one-submission lock, R4/R5) + WO-52 (claim-line construction + note→invoice resolution, R2/R16) shipped. 70-100 day milestone; remaining slices tracked below. |
| M4 | Payments & cash depth | `Planned` |
| M5 | Transport vertical phase 2 — recovery intelligence | `Planned` |
| M6 | Integrations & enterprise go-live | `Planned` |

**Test suite:** 761 → 1169 → 1216 → 1247 → 1259 → 1290 passed (+529 total, +31 this session), 10 skipped
(pg-only, verified separately on real Postgres), 0 known regressions, as of WO-52.

---

## M3 — In Progress

- [x] **WO-49** — `Completed` — M3 opener: the transport-vertical foundation. `app/models/transport/
  vat_claim.py` (`VatRefundClaim`/`VatRefundClaimLine`, the `(org, entity, refund_country, ref_period)`
  claim grain, R1 — `entity_id` reuses the existing `issuer_profiles` registry rather than a new
  legal-entity table); `app/services/transport/claim_gates.py::is_synthetic()` (R3, the ONE predicate
  every future lock/checklist/readiness/workbook gate must import — harvested verbatim from
  `BA_fleet_fuel.md` C2); `app/services/transport/claim.py::get_or_create_claim` (idempotent on the
  grain, R1's acceptance test verbatim); the `transport` module entitlement (default OFF, absent from
  every plan's module set — pricing is owner-blocked, `docs/DECISIONS-NEEDED.md` §10); 4 new
  permissions (`vat.read/write/submit`, `transport.read`) in all 8 `ROLE_PERMISSIONS` rows ahead of any
  route (ADR-P3 rule 5); migration `02d418169f97` (2 new tenant tables, RLS in the same migration,
  defense-in-depth CHECK constraints for the period shape and R11's "goods code 9 never" rule); the
  `test_boundaries.py` cross-domain-import CI assertion ADR-0023 promised. Explicitly NOT built (future
  M3 work orders, `ARCH_plan.md` G2.2 onward): the lock table, any gate (period-end/deadline/minimum/
  checklist/receipt-waiver), fee freezing, status derivation, goods-code mapping, `fuel_transactions`,
  the monthly close job, capture/entity-resolution, and every `api/routes/transport/*` route. Detail:
  `docs/plan/plan-a/wo/WO-49-G1.1-G2.1-G2.3.md`.

- [x] **WO-50** — `Completed` — G1.2: the typed `fuel_transactions` model
  (`app/models/transport/fuel_transaction.py`) per `BA_fleet_fuel.md` section 4.2 (the canonical
  transaction schema) + section 8.1 items 4-6 (no duplicated positional schema; split the overloaded
  `note` into `invoice_ref`+`provenance_note`; a real natural key). Note: `ARCH_plan.md` tags this task
  R29/R30, but those R-numbers are actually about engine read-only ownership and the separate claims
  store — not the transaction schema; corrected in `docs/plan/plan-a/wo/WO-50-G1.2.md`. Natural key
  `(org, entity, supplier, period, line_seq)` — caller-assigned, deterministic line position — makes
  ingestion insert-or-no-op, never Fleet Fuel's DELETE-by-period; `app/services/transport/product_group.py
  ::derive_product_group()` centralizes the PROMO → HVO → {AdBlue,Parking,Toll/Fees} → Diesel →
  Service/Other precedence the same way `is_synthetic()` is centralized; `app/services/transport/
  fuel_ingest.py::ingest_transaction()` gates on the module entitlement first, resolves the entity via
  `issuer.get_by_id` (opaque 404), `q2`-quantizes every monetary column while leaving `qty` deliberately
  unrounded, audits exactly once per real insert. `invoice_id` is a nullable FK into `invoices` (ADR-P3
  rule 1) — the same table `vat_claim_lines.invoice_id` (WO-49) points at, so the two transport tables
  relate only through the shared AP invoice, never a new transport-internal cross-reference. Migration
  `fc45baaf3283` (1 table, RLS in the same migration); RLS proven on real Postgres (cross-tenant SELECT
  returns zero rows, cross-tenant INSERT blocked by `WITH CHECK`). 68 tables, 74 revisions. Detail:
  `docs/plan/plan-a/wo/WO-50-G1.2.md`.

- [x] **WO-51** — `Completed` — G2.2: the one-invoice-one-submission lock table. `app/models/transport/
  lock.py` (`VatClaimedInvoice`, `UNIQUE(org_id, entity_id, refund_country, supplier, invoice_ref)` IS
  the lock, R4 — `entity_id`/`refund_country` denormalized so the constraint spans EVERY claim, not just
  the one that currently holds a row, and widened with `org_id` per this codebase's standing convention
  over the harvested BA text, which predates multi-tenancy); `app/services/transport/lock.py::
  submit_claim` (a minimal stub `draft`→`submitted` transition — acquires one lock row per invoice via a
  plain ORM INSERT, never an upsert, in the SAME flush as the claim's status mutation, so a lost race
  raises `IntegrityError` and rolls back the whole transition, nothing partially applied) and
  `withdraw_claim` (R5 — the ONLY function that deletes a lock row, proven both structurally via a
  grep-based test and behaviorally via a test that directly mutates a claim's `status` and asserts no
  lock release cascades). Three composite FKs off the lock row: `(org_id, claim_id)` CASCADE into
  `vat_refund_claims`, `(org_id, entity_id)` RESTRICT into `issuer_profiles`, `(org_id,
  fuel_transaction_id)` RESTRICT into `fuel_transactions` (WO-50's composite unique constraint existed
  specifically for this FK target — one representative transaction row per lock; protecting every row
  sharing an `invoice_ref` is a future close/re-close guard's job, flagged explicitly as NOT solved by
  this FK alone). Migration `dea0a87e6b0d` (1 table, RLS in the same migration). The headline proof: a
  real-Postgres concurrency test (`tests/test_transport_lock_concurrency.py`, added to the CI `postgres`
  job) fires two DIFFERENT claims racing `asyncio.gather` over the SAME invoice key — exactly one wins,
  the loser's status reads back its unchanged pre-submission value from a fresh query, proving the whole
  transaction rolled back, not just the lock insert; a second test proves `withdraw_claim` then frees the
  key for a third claim. All 6 pre-existing pg-only files (`test_rls.py`, `test_rls_connection_reuse.py`,
  `test_numbering_concurrency.py`, `test_payment_run_pay_concurrency.py`,
  `test_credit_note_lock_concurrency.py`, `test_expense_decision_concurrency.py`) re-verified green on
  the same scratch cluster. 69 tables, 75 revisions. Detail: `docs/plan/plan-a/wo/WO-51-G2.2.md`.

- [x] **WO-52** — `Completed` — G2.4: claim-line construction + note→invoice resolution.
  `app/services/transport/invoice_match.py::resolve_invoice_ref` — the ONE C3 resolution order (two
  note-matching heuristics — prefix / stem-contained, a documented interpretation of an underspecified
  BA phrase — then the admin-curated override, only consulted once both heuristics fail to resolve
  uniquely and NEVER displacing a heuristic match, then the sole-registered-invoice fallback, else
  UNMATCHED); `app/models/transport/note_override.py::VatNoteInvoiceOverride` (R16's admin override
  table, `ondelete=CASCADE` on the target FK — a real defect caught live: a composite-FK `SET NULL`
  would try to null the NOT-NULL `org_id` column too, so CASCADE deletes the dead override row instead);
  `app/services/transport/claim_lines.py::build_claim_lines` (materializes the LIVE, unfrozen
  `VatRefundClaimLine` rows for a `draft` claim from its in-scope `fuel_transactions` — R2, one row per
  (invoice, product_group), never an `ALL:` aggregate; refuses a non-draft claim; only ever touches
  `frozen_at IS NULL` rows, future-proofing G2.5's freeze). Two new read-only AP-domain seams
  (`app.services.invoices`, `app.services.vendors.get_by_name`) fill the `invoice_service` gap ADR-0023
  always named, so `services/transport/*` never imports `app.models.invoice`/`app.models.vendor`
  directly (`test_transport_services_do_not_import_other_domain_models` stays green). Migration
  `4cb7fca7e508` (1 table, RLS in the same migration). `tests/test_tenancy_parity.py`'s exemption
  registry gained a `vat_note_invoice_overrides` row (no route yet to drive an HTTP-level probe through).
  70 tables, 76 revisions, 83 service modules. Detail: `docs/plan/plan-a/wo/WO-52-G2.4.md`.

---

## M2 — In Progress

- [x] **WO-47** — `Completed` — Quota/usage-limit model now keys off the org's `plan`
  (`app.services.plans.PLANS`), not the acting user's role (the plan flagged this as must-fix
  "before the first invoice, not after") — every member of an org shares one org-wide cap;
  `role_policies`→`plan_policies`. Preserves the never-lose-a-document-on-limit guardrail (every
  quota check still runs before anything is persisted; block-at-the-cap, since auto-charge overage
  needs live billing, still owner-blocked). Detail: `docs/plan/plan-a/wo/WO-47-H13.md`.
- [x] **WO-48** — `Completed` — Dogfood fallback (H1.6): `app.services.platform_billing` invoices
  InvoiceIQ's own paying tenants through the platform's own AR module (issuer registry, gap-free
  numbering, PDF/XML, send, dunning — all pre-existing, zero new delivery code) once per calendar
  month, whenever no live billing provider is configured. Off by default (`platform_org_id` unset);
  applies a 0% VAT placeholder pending the seller-of-record decision (§2/§2b). Revenue is no longer
  blocked on Stripe/EveryPay credentials landing. Detail: `docs/plan/plan-a/wo/WO-48-H16.md`.

### M2 — Blocked on owner/business decisions (not code-blocked; tracked in `docs/DECISIONS-NEEDED.md`)

- [ ] **Stripe live** — Checkout, Billing Portal, signed webhook, Billing Meter. Needs production
  Stripe credentials from the owner. **Fallback shipped (WO-48):** AR-module dogfood invoicing —
  revenue is not blocked on this; activation is an operational config step, `docs/DECISIONS-NEEDED.md`
  §2b.
- [ ] **Seller-of-record VAT process** — Stripe Tax vs. an explicit alternative; a finance decision.
- [ ] **Plan ladder reconciliation (H1.2)** — code implements trial/starter/pro/enterprise
  (€0/€29/€99/custom); the pricing hypothesis doc proposes a different Free/€39/€99/€249/Enterprise +
  Practice ladder. They conflict; the owner must pick one. WO-47's quota fix already uses whichever
  ladder is live in code today (indicative defaults, sysadmin-overridable) — this decision changes
  only `plans.py::PLANS`, not the enforcement mechanism. `docs/DECISIONS-NEEDED.md` §2a.

---

## M1 — Completed (WO-12…46)

Epics F1.1 (master-data/document screens) · C1.5–C1.9 (currency/dimension registries, multi-currency
reporting, dead-state removal, reclaimable-VAT fix) · I1.1–I1.3/I1.5 (composed dashboard, grouped nav
IA, honest cash-position label, report writers) · A1.5 (8 business roles reachable) · E1.1–E1.7
(capture-review UI, line-item provenance, hash re-upload detection, Mailgun adapter, extraction
learning loop) — all shipped, tested, documented. Full list: `docs/plan/plan-a/wo/WO-12*.md` through
`WO-46*.md`.

### 4-agent SaaS review-board audit (R1–R19) — Completed except 2 open

| Item | Status | Closed by |
|---|---|---|
| R1 — CSV formula-injection across 3 exports | ✅ Completed | WO-29 |
| R2 — Credit-note creation had no row lock (over-crediting race) | ✅ Completed | WO-26 |
| R3 — Seed data self-contradicted (Cash Position vs Invoices) | ✅ Completed | WO-28 |
| R4 — Expense-decision had no concurrency guard | ✅ Completed | WO-30 |
| R5(b) — Enterprise self-upgrade-for-free billing bypass | ✅ Completed | WO-31 |
| R5(a) — Self-serve billing collects zero real payment | 🔶 **Owner-blocked** | → M2 (Stripe live) |
| R6 — Reimbursement payout had no maker≠checker SoD | ✅ Completed | WO-32 |
| R7 — ClamAV fail-closed branch had no test coverage | ✅ Completed | WO-33 |
| R8 — OIDC discover/JWKS had no SSRF guard | ✅ Completed | WO-37 |
| R9 — Duplicate CSV-sanitization helper (3×) | ✅ Completed | WO-36 |
| R10 — `LocalStorage._path` bare-`startswith` containment | ✅ Completed | WO-42 |
| R11 — Stale SSO `client_secret` TODO comment | ✅ Completed | WO-41 |
| R12 — Stale README/ARCHITECTURE.md counts | ✅ Completed | WO-10 + ongoing truth-up (verified count now in README) |
| R13 — `test_refresh_owner_only_and_graceful` didn't test "owner only" | ✅ Completed | WO-38 |
| **R14** — No application-owned backup/restore tooling | 🔴 **Decision-gated** | Needs an owner/ops decision (infra-level DR runbook vs. app-level capability) before any code — see `docs/audit/remediation-roadmap.md` R14 detail |
| R16 — AR Void/Write-off had no confirmation dialog | ✅ Completed | WO-34 |
| R17 — Payment-run Cancel had no confirmation | ✅ Completed | WO-39 |
| R18 — Billing downgrade silently disabled modules | ✅ Completed | WO-35 |
| **R15** — No load/concurrency/large-dataset perf harness | ⚪ **Backlog (P3)** | Standalone build, not started — larger effort, own future work order |
| **R19** — No guided onboarding/setup-wizard checklist | ⚪ **Backlog (P3)** | Standalone build, not started — larger effort, own future work order |

### UX/UI redesign audit — Phase 1 complete, implementation started

- [x] **Audit** — `Completed` — `docs/design/UX-AUDIT.md`. Verdict: the WO-17 grouped sidebar nav is
  already good, do not rebuild; two CRITICAL findings (silent-failure async states on 26 pages;
  unlabeled form controls, WCAG 1.3.1/4.1.2) and one HIGH (design system built but ~unused —
  migration, not new-build).
- [ ] **WO-45-UX1** — `Planned` — fix `QueryState`'s error branch, adopt `QueryState`+`PageHeader` on
  the 8 money-bearing pages, unclip the Invoices table, add a focus ring to `.btn`. Fully specified,
  not yet implemented: `docs/plan/plan-a/wo/WO-45-UX1-async-state-and-page-header.md`.
- [ ] Further UX slices (design-system migration across remaining pages, form-label pass, nav collapse
  rail/group-collapse/breadcrumbs, orphaned-route wiring for `/issuer` and `/reimbursements`) —
  `Backlog`, scoped in `docs/design/UX-AUDIT.md`'s phased plan, to be written up as WO-48+ in turn.

---

## M0 — Completed (WO-1…11)

Structural authorization + CI coverage gate · vendor bank-detail dual control (the IBAN fraud vector)
· partners router lockdown · per-request org-suspension + session revocation · mandatory inbound-email
secret · PII quarantine (tree + full-history CI scan) · one validation engine · one FX convention (the
SEPA wrong-currency bug) · payment-run maker≠checker/export-once/MsgId · docs truth-up + ADRs + the
tenancy-parity probe · `users.org_id`→memberships (B1.5). Full detail: `docs/M0-exit-gate.md`.

---

## Historical note

Everything below this line is the original 2026-07-27 audit snapshot, preserved for traceability. Every
item on it is now closed or reclassified above — do not treat it as current; the tables above supersede
it entirely. The original per-item detail sections it links to remain live in `docs/audit/`.

<details>
<summary>Original 2026-07-27 board (click to expand — superseded)</summary>

This board is seeded from the Phase 1-11 independent 4-agent SaaS review board audit (see
`docs/audit/`, run on branch `claude/bidit-invoice-data-analytics`, 2026-07-27). It reflects the
**debate-adjusted** priorities in `docs/audit/remediation-roadmap.md`. Every P0/P1 item starts as
**Approved** (ready for a work order); every P2 starts as **Backlog**; P3/P4 items are also
**Backlog** (lower priority, schedule opportunistically). No disputed/rejected findings occurred in
this audit round — see `docs/audit/agent-debate.md`'s "Disputed / rejected findings" section.

Statuses: `Backlog` · `Approved` · `In Progress` · `Blocked` · `Testing` · `Review` · `Completed` · `Rejected`

Implementation of these items is explicitly **out of scope for this pass** — each item becomes its own
future work order (WO-26+), reviewed one change at a time, matching the WO-1..WO-25 pattern already used
in this repo (see `git log` / `docs/plan/`).

### P0 — blocks any pilot (Approved)

- [x] **R2** — Credit-note creation has no row lock, violating the codebase's own non-negotiable
  invariant on over-crediting (reproduced live). Evidence: `docs/audit/functional-audit.md` §2.1,
  `docs/audit/agent-debate.md` §1.
- [x] **R3** — Demo/seed data self-contradicts: Cash Position/Payment Runs show €0 owed while
  Invoices lists >€1M. Evidence: `docs/audit/commercial-readiness.md` §3, `docs/audit/agent-debate.md` §8.

### P1 — blocks general (self-serve) release (Approved)

- [x] **R1** — CSV formula-injection sanitization inconsistently applied across 3 financial exports.
  Evidence: `docs/audit/functional-audit.md` §3.1, `docs/audit/security-findings.md` §2.4.
- [x] **R4** — Expense-approval decision has no optimistic-concurrency guard or row lock.
  Evidence: `docs/audit/test-baseline.md` finding #2.
- [ ] **R5** — Self-serve billing collects zero real payment today; Enterprise tier is self-upgradable
  for free even with billing wired (`price_eur=None` bypass). Split: the free-upgrade bypass closed
  (WO-31); the "collects zero real payment" half is owner-blocked, tracked under M2 above.
  Evidence: `docs/audit/commercial-readiness.md` §7.

### P2 — should fix before general release (Backlog)

- [x] **R6** — Reimbursement payout had no maker≠checker (SoD) control.
- [x] **R7** — ClamAV fail-closed malware-scan branch had zero test coverage.
- [ ] **R14** — No application-owned backup/restore tooling exists; decision-gated. Still open —
  tracked above.
- [x] **R16** — AR "Issue" screen: destructive actions had no confirmation dialog.
- [x] **R18** — Billing downgrade silently disabled modules with no confirmation.

### P3 — backlog / hardening (Backlog)

- [x] **R8** — OIDC `discover()`/`fetch_jwks()` had no SSRF guard.
- [x] **R9** — Duplicate CSV-sanitization helper implemented 3x.
- [x] **R13** — `test_fx.py::test_refresh_owner_only_and_graceful` didn't actually test "owner only."
- [ ] **R15** — No load/concurrency/large-dataset performance testing harness. Still open — tracked above.
- [x] **R17** — Payment-run "Cancel" button fired with no confirmation.
- [ ] **R19** — No guided onboarding/setup-wizard checklist. Still open — tracked above.

### P4 — informational / doc hygiene (Backlog)

- [x] **R10** — `LocalStorage._path` containment check used bare `startswith`.
- [x] **R11** — Stale `# TODO: secret store` comment on `SsoConnection.client_secret`.
- [x] **R12** — Root `README.md`/`ARCHITECTURE.md` were stale.

### Verified controls — no action required (not tasks; recorded for traceability)

These were raised during the audit (some at P1) but resolved through adversarial debate as
**confirmations of existing strength**, not defects — see `docs/audit/agent-debate.md` and
`docs/audit/remediation-roadmap.md`'s "Verified controls" section:

- Tenant isolation (query filter + ORM guard + Postgres RLS with FORCE) — independently reproduced
  live against a real Postgres cluster twice.
- Structural, CI-gated route authorization (`test_authz_coverage.py`) — recommend keeping this a
  required, unfiltered CI gate.
- Upload/attachment security gate (`filesec.py`) — covers all 8 intake paths, live exploit tests pass.
- `docs/plan/plan-a/ARCH_plan.md`'s prior risk claims (vendors/partners authz, route-level
  `_reconcile`, hardcoded EUR) were stale/false against source at audit time — since superseded by the
  banner fix (2026-07-28); the document is current again.

*Backlog items predating this audit, if any existed at the repo root, are preserved in
`docs/BACKLOG.md` (pre-existing) — this file is the audit-derived task board and does not duplicate or
supersede that doc; see `docs/BACKLOG.md` for other in-flight work not covered by this audit round.*

</details>
