# Remediation Roadmap — Merged, De-duplicated, Prioritized

**Source:** `docs/audit/functional-audit.md`, `docs/audit/system-architecture.md` /
`docs/audit/security-findings.md`, `docs/audit/test-baseline.md`, `docs/audit/commercial-readiness.md`, and
`docs/audit/agent-debate.md`. Severities below are the **debate-adjusted** severities where a debate
happened (9 of the findings below were individually cross-examined — see `agent-debate.md` for full
rationale on each). This document is the input to the next phase of implementation work orders (WO-26+),
run separately, one reviewable change at a time — **no fixes were attempted in this pass.**

Priority scale: **P0** (blocks any pilot/customer data — financial-correctness or trust-destroying) ·
**P1** (blocks general release; fix before broad rollout) · **P2** (should fix before general release;
schedulable) · **P3** (backlog, quality/hardening) · **P4** (informational / doc hygiene / no material risk).

---

## Milestone plan

### Milestone A — Must fix before ANY pilot (including a hand-held, contract-supervised pilot)
| ID | Item | Priority |
|---|---|---|
| R2 | Credit-note creation has no row lock (over-crediting race) | **P0** |
| R3 | Demo/seed data self-contradicts (Cash Position/Payment Runs vs Invoices) | **P0** |
| R1 | CSV formula-injection unprotected on 3 financial exports | **P1** — CLOSED (WO-29) |

*Rationale:* R2 is a reproduced financial-correctness bug that would corrupt real AR data the moment two
credit-note requests race, which can happen even under a single supervised pilot user (double-click, two
tabs, a retried integration call) — this must not touch a single customer's ledger. R3 blocks the ability to
even *demo* the product credibly, which is a precondition for any pilot conversation, and the fix is
zero-risk (confined to `seed.py`). R1 protects the pilot's own real bank-payment export the moment the AP
module goes live with a real vendor.

### Milestone B — Must fix before General (self-serve) Release
| ID | Item | Priority |
|---|---|---|
| R4 | Expense-approval decision has no optimistic-concurrency/row lock | **P1** — CLOSED (WO-30) |
| R5 | Self-serve billing collects no real payment; Enterprise tier self-upgradable for free | **P1** — (b) CLOSED (WO-31), (a) still open |
| R6 | Reimbursement payout has no maker≠checker (SoD) control | **P2** — CLOSED (WO-32) |
| R7 | ClamAV fail-closed malware-scan branch has zero test coverage | **P2** — CLOSED (WO-33) |
| R14 | No application-owned backup/restore tooling exists | **P2** — decision-gated, not code (see below) |
| R16 | AR "Issue" screen: destructive actions (Void/Write off) have no confirmation | **P2** — CLOSED (WO-34) |
| R18 | Billing downgrade silently disables modules with no confirmation | **P2** — CLOSED (WO-35) |

*Rationale:* none of these block a **contract-supervised** pilot where InvoiceIQ's own team curates the
data, billing is arranged out-of-band, and expense-module concurrent double-decisions are operationally
unlikely under close supervision — but every one of them becomes live risk the moment the product is
self-serve and unsupervised. **Compensating control for the pilot window on R5:** keep self-serve billing
config (`billing_provider`) unset/`none` and do not advertise the Billing page's plan-switch as a live
purchase path until R5 is closed.

### Milestone C — Backlog (not release-blocking)
| ID | Item | Priority |
|---|---|---|
| R8 | OIDC discover()/fetch_jwks() has no SSRF guard | P3 — CLOSED (WO-37) |
| R9 | Duplicate `_safe`/`_safe_cell` CSV-sanitization helper (3 copies, no shared module) | P3 — CLOSED (WO-36) |
| R13 | `test_fx.py::test_refresh_owner_only_and_graceful` doesn't test "owner only" | P3 — CLOSED (WO-38) |
| R15 | No load/concurrency/large-dataset performance testing has been performed | P3 |
| R17 | Payment-run "Cancel" button fires with no confirmation | P3 |
| R19 | No guided onboarding/setup-wizard checklist | P3 |
| R10 | `LocalStorage._path` containment check uses bare `startswith` (not currently reachable) | P4 |
| R11 | Stale TODO on `SsoConnection.client_secret` contradicts accurate docstring | P4 |
| R12 | Root `README.md`/`ARCHITECTURE.md` are stale (module/route/migration counts) | P4 |

---

## P0 items — full detail

### R2 — Credit-note creation has no row lock, violating the codebase's own non-negotiable invariant on over-crediting
- **Priority:** P0 (debate-raised from P1 submission — see `agent-debate.md` §1)
- **Problem statement:** `POST /issued-invoices/{id}/credit-note` reads `original.credited_total` without a
  row lock, computes the remaining creditable amount from that stale read, and writes back
  `credited_total = min(already + this_credit, total)`. Two concurrent requests against the same invoice can
  both read the same stale value and one write is lost, leaving `credited_total` understated and
  `effective_total` (the overpay cap `record_payment` trusts) overstated.
- **Evidence:** `functional-audit.md` §2.1 (`backend/app/api/routes/issued.py:620` vs. `:770`); confirmed and
  strengthened in `agent-debate.md` §1 with a live Postgres reproduction (two concurrent €300/€400 credit
  notes against a €1000 invoice left `credited_total` at €300, not €700).
- **Proposed solution:** pass `lock=True` to the existing `_load()` call in `create_credit_note`, mirroring
  the already-tested pattern used in `record_payment` (`issued.py:770`). No new helper, no schema change.
- **Risk of the fix itself:** minimal. `_load`'s lock branch is already exercised in production by
  `record_payment`; adding a second call site with the same lock does not introduce a new deadlock class
  (lock ordering — invoice row, then issuer row — matches the only other lock already acquired in this
  function). No API shape or business-logic change.
- **Dependencies:** none. Independent of R1/R4/R5.
- **Acceptance criteria:**
  1. `create_credit_note` acquires `with_for_update` on the original invoice row before reading
     `credited_total`.
  2. A new Postgres-only concurrency test (mirroring `test_payment_run_pay_concurrency.py`) fires two
     genuinely concurrent partial credit-note requests against the same invoice and asserts the resulting
     `credited_total` equals the sum of both credits (not a lost update).
  3. Existing `test_credit_notes.py` suite continues to pass unmodified in behavior (same API responses for
     the non-concurrent case).
  4. `docs/plan/shared/00_MASTER_CONTEXT.md` invariant §4/13 documentation is either already satisfied or
     updated to reference the new test.
- **Follow-up (WO-27, P0, closed):** proving R2's fix (`tests/test_credit_note_lock_concurrency.py`)
  required combining real Postgres + the full HTTP auth chain + more than one authenticated request on a
  shared pool for the first time, which surfaced a second, previously-undiscovered defect one layer down:
  every RLS policy's `current_setting('app.current_org', true) IS NULL` "unscoped" check silently stops
  matching after the FIRST scoped transaction on a physical connection (Postgres never restores a custom
  GUC to NULL once `set_config` has touched it — it sticks at `''`), hiding rows for any unscoped/
  re-authenticating request that reuses a warmed pooled connection — i.e. any real deployment under load.
  Fixed by migration `6fec8c88ba7c` (every policy now also treats `''` as unscoped); see
  `docs/architecture/adr/0028-rls-unscoped-guc-sticky-empty-string.md`,
  `tests/test_rls_connection_reuse.py` (red-then-green proof), and `docs/plan/plan-a/wo/WO-27.md`.
  `test_credit_note_lock_concurrency.py` now runs on a normal reused pool (no more `NullPool` workaround).

### R3 — Demo/seed data contradicts itself (Cash Position/Payment Runs show €0 while Invoices lists >€1M) — **CLOSED (WO-28)**
- **Priority:** P0 (debate-confirmed, no change — see `agent-debate.md` §8)
- **Closed by:** `docs/plan/plan-a/wo/WO-28-R3.md`. `app/seed.py` now collects every seeded AP
  invoice into `ap_invoices` and, after the existing post-creation flush, drives each one
  through the REAL endpoint functions (`invoice_review.submit`/`approve`/`transition`,
  `payment_runs.create_run`/`approve_run`/`pay_run`, called directly — not over HTTP, but the
  exact same code FastAPI dispatches to) via a new `_drive_ap_workflow()` helper: every
  `status=paid` invoice reaches `workflow_state=paid` through a real, batched payment run
  (`override_sod=True`, audited — the demo org's single `is_platform_admin` owner is its own
  maker/checker/payer, the same escape hatch WO-9 gives a real solo-admin org); every
  `pending`/`overdue` invoice reaches an open-payable state (`approved`, some further to
  `scheduled_for_payment`). Regression-proven by
  `backend/tests/test_seed.py::test_seed_drives_ap_invoices_through_real_workflow` (asserts no
  invoice is left at `workflow_state=draft`, every `status=paid` invoice is
  `workflow_state=paid` and fully settled, every open invoice is in an open-payable state, and
  `cash_position.summary()` / `payment_run.payable_invoices()` / a paid `PaymentRun` row are all
  non-empty for the freshly seeded demo org).
- **Problem statement:** `backend/app/seed.py` sets the legacy `Invoice.status` enum directly on all 83
  seeded AP invoices but never drives them through the real `/submit`→`/approve`→schedule→`/pay` workflow
  endpoints, so `workflow_state` stays at `draft` for every one of them. Every downstream "payables" surface
  (`cash_position.py`, `payment_run.py`, the Dashboard) correctly and deliberately reads `workflow_state`
  (not the legacy `status` the Invoices list badge reads), so those surfaces show €0.00 owed while Invoices
  shows >€1,063,592.82 — a page-to-page contradiction on the two numbers a finance buyer cares about most,
  on the officially documented, customer-facing demo login.
- **Evidence:** `commercial-readiness.md` §3, independently reproduced end-to-end in `agent-debate.md` §8
  (fresh seed run, direct SQL query, live `cash_position.summary()` call, root-caused to `app/seed.py`).
- **Proposed solution:** modify `app/seed.py` to drive its AP invoices through the real workflow endpoints
  (`invoice_workflow`/`invoice_review` submit/approve, `payment_run` schedule/pay) instead of hand-setting
  the legacy `status` enum, so `workflow_state` and `status` stay in sync exactly as they do for a real
  customer's data (per the documented contract in `app/models/invoice.py:38-41` and the real sync point in
  `payment_run.py:369-370`).
- **Risk of the fix itself:** none to production code — the fix is 100% confined to a demo-data generator
  script (`app/seed.py`), not the application's real workflow, authz, or tenant-isolation logic. Worst case
  is a slower seed run (calling real endpoints/services instead of direct inserts) or a seed-script bug that
  only affects demo data, never a live customer's database.
- **Dependencies:** none. Should be done in the same change as, or immediately after, any other demo/seed
  hygiene work; does not block or get blocked by R1/R2/R4/R5.
- **Acceptance criteria:**
  1. After a fresh `python -m app.seed` run, `SELECT workflow_state, count(*) FROM invoices GROUP BY
     workflow_state` shows a realistic mix (not 100% `draft`) that is consistent with the legacy `status`
     mix already seeded (paid → `workflow_state=paid`; overdue/pending → an appropriate payable state).
  2. `/dashboard`, `/cash-position`, and `/payment-runs`, driven against the freshly seeded demo org, show
     non-zero, mutually consistent payables/overdue figures that reconcile with what `/invoices` displays.
  3. A regression test (or an assertion added to an existing seed-smoke test, if one exists) fails if the
     two fields are ever allowed to diverge again in the seed script.

---

## P1 items — full detail

### R1 — CSV formula-injection sanitization is inconsistently applied across financial exports — **CLOSED (WO-29)**
- **Priority:** P1 (debate-confirmed, no change — see `agent-debate.md` §2)
- **Problem statement:** three CSV export paths write attacker-influenceable free text directly into
  `csv.writer` with no formula-injection neutralization, while three sibling exporters in the same codebase
  already implement and use the correct `_safe()` pattern: `payment_run.py::export_csv` (writes
  `inv.invoice_number` raw — the bank-payment export), `reimbursement.py::export_csv` (writes
  `employee_name`/`title` raw — the payroll-adjacent payout export), and `explore.py::to_csv` (writes
  dimension values including `Vendor.name` raw — the general analytics export).
- **Evidence:** `functional-audit.md` §3.1 (payment_run/reimbursement instances) and
  `security-findings.md` §2.4 (explore.py instance); merged and confirmed in `agent-debate.md` §2.
- **Proposed solution:** extract the existing `_safe()`/`_safe_cell()` logic (currently duplicated three
  times — see R9) to one shared helper (e.g. `app/core/csv_safety.py`) and apply it in all three unsafe
  writers' cell-writing loops.
- **Risk of the fix itself:** minimal. It's a pure string-prefix transform on cell values that only changes
  output for strings starting with `=`, `+`, `-`, `@`, tab, or CR — no change to totals, amounts, or any
  numeric/financial calculation. Already proven safe by the three sibling exporters' existing tests.
- **Dependencies:** naturally paired with R9 (the dedup); R9 is not required first but doing both together
  avoids a fourth reviewable change.
- **Acceptance criteria:**
  1. `payment_run.export_csv`, `reimbursement.export_csv`, and `explore.to_csv` all neutralize any cell value
     starting with `=+-@\t\r` by prefixing a single quote, matching `erp_export._safe`'s behavior exactly.
  2. New unit tests (mirroring `test_erp_export.py`'s existing formula-injection test) assert each of the
     three writers' output for a `=HYPERLINK(...)`-style input in the relevant free-text field.
  3. No change to non-malicious CSV output (byte-for-byte identical for values not starting with a trigger
     character).
- **Closed by:** `docs/plan/plan-a/wo/WO-29-R1.md`. A new shared `app/core/csv_safety.py::sanitize_cell`
  (OWASP leading-quote mitigation) is now applied to the free-text columns of `payment_run.export_csv`
  (`run.reference`/`inv.invoice_number`), `reimbursement.export_csv` (`batch.reference`/`employee_name`/
  `title`), and `explore.to_csv` (dimension values) — amount/currency/method/measure columns are
  deliberately left untouched (numeric/enum, not free text). As a low-risk side effect (naturally paired
  per this item's own "Dependencies" note), the three pre-existing duplicated helpers —
  `erp_export._safe`, `audit_export._safe`, `report_writers._safe_cell` — now delegate to the same shared
  function under their original local names, a down payment on R9 (not a full close of it). Red-then-green
  proof per export path: `tests/test_payment_runs.py::test_export_csv_is_formula_injection_safe`,
  `tests/test_reimbursement.py::test_export_csv_is_formula_injection_safe`,
  `tests/test_explore.py::test_to_csv_is_formula_injection_safe`, plus
  `tests/test_csv_safety.py` (unit tests on the shared helper). `test_erp_export.py`/`test_audit_export.py`/
  `test_report_writers.py` pass unmodified, proving the dedup is behavior-preserving. This was the last
  open Milestone A item — **Milestone A is now fully closed** (R2 closed by WO-26/WO-27, R3 by WO-28, R1 by
  WO-29).

### R4 — Expense-approval decision has no optimistic-concurrency guard or row lock — **CLOSED (WO-30)**
- **Priority:** P1 (debate-confirmed, no change — see `agent-debate.md` §6)
- **Closed by:** `docs/plan/plan-a/wo/WO-30-R4.md`. `ExpenseReport` gained a `version` column
  (migration `1b4cf6cc802f`, `server_default="1"`, mirrors `Invoice.version`/
  `ReimbursementBatch.version`); `ExpenseDecision.version` is now required; `expenses.py::_load`
  gained a `lock: bool = False` keyword taking `.with_for_update()` (mirrors
  `reimbursements.py::_load`); `decide()` loads with `lock=True`, 409s on a version mismatch, and
  bumps the version once per successful decision — one lock/check/bump point covers approve,
  reject, return_for_correction, mark_for_reimbursement AND mark_reimbursed uniformly (they share
  the single `decide()` handler, so the `mark_reimbursed` bypass the roadmap called out is closed
  by construction, not as a separate fix). `ExpenseDetail.tsx` sends `version: r.version` on every
  decision (mirrors `PaymentRuns.tsx`'s existing pattern). Red-then-green proved on a scratch
  Postgres 16 cluster: `tests/test_expense_decision_concurrency.py` (two designated approvers
  firing genuinely concurrent `approve` decisions on the same open pending step) failed 4/4 runs
  with the lock+bump reverted (a nondeterministic mix of double-`200`s and version-count
  mismatches — not a fixed, single symptom) and passed 4/4 runs with the fix restored; asserts
  exactly one `200`/one `409`, exactly one `STEP_APPROVED` row (no lost update on the step
  itself), and exactly one `WebhookDelivery` for `expense.approved` (no double fan-out to a real
  integration). `tests/test_expense_decision_version.py` (SQLite, every CI job) proves the
  stale-version 409 and that the loser's decided state is left byte-identical to the winner's.
  Every existing test that posts `/decision` was updated to supply the now-required `version`
  field — a mechanical wire-shape fix, zero behavior-assertion changed.
- **Problem statement:** `POST /expenses/{id}/decision` (`decide()`) reads the expense report's status with
  a plain `select()` (no `.with_for_update()`) and `ExpenseDecision` carries no `version` field, unlike every
  other money-adjacent mutation route in the codebase (`payment_runs.py`, `receipts.py`, `reconciliation.py`,
  `issuer.py`, `issued.py`, `reimbursements.py` all lock and/or version-check). Two concurrent decisions on
  the same pending step (two approvers, or a double-click) can both succeed, producing duplicate audit
  entries and duplicate, un-deduplicated outbound webhooks. A separate, live, UI-reachable `mark_reimbursed`
  action (`ExpenseDetail.tsx`) bypasses the one path (`reimbursements.py`) that IS locked.
- **Evidence:** `test-baseline.md` finding #2; strengthened in `agent-debate.md` §6 (the `mark_reimbursed`
  bypass and un-deduplicated `webhooks.emit()` double-fire).
- **Proposed solution:** add a `version` field to `ExpenseDecision` (and the report's read/write path), check
  it the same way `invoice_review.py`'s submit/approve do (409 on mismatch), and take a
  `.with_for_update()` lock in `expenses.py::_load` for the decision and `mark_reimbursed` code paths,
  mirroring `reimbursements.py`'s existing pattern.
- **Risk of the fix itself:** low. Adds a guard, doesn't remove one; follows an established, already-reviewed
  pattern in three sibling modules; does not touch authz or tenant scoping. The only client-visible change is
  a new required `version` field on the decision request (a breaking API change for any existing integration
  — needs a documented migration note / grace period if external API consumers exist).
- **Dependencies:** none functionally, but should be scheduled with frontend work (`ExpenseDetail.tsx` needs
  to pass the row's current `version` on `decide`/`mark_reimbursed` calls).
- **Acceptance criteria:**
  1. `ExpenseDecision` requires a `version`; a stale version returns 409, matching the pattern in
     `test_invoice_review_e2e.py`'s stale-version tests.
  2. `expenses.py::_load` takes `.with_for_update()` on the decision and `mark_reimbursed` paths.
  3. A new Postgres-only concurrency test (mirroring `test_payment_run_pay_concurrency.py`) fires two
     concurrent decisions on the same pending step and asserts exactly one succeeds.
  4. `webhooks.emit()` is not double-fired for the same logical decision (verified by the new concurrency
     test asserting a single `WebhookDelivery` row is created).

### R5 — Self-serve billing collects zero real payment today; Enterprise tier is self-upgradable for free even with billing wired
- **Priority:** P1 (debate-confirmed, and found materially worse than the original submission — see
  `agent-debate.md` §9)
- **Problem statement:** with the shipped default (`billing_provider=none`), `PUT /billing/plan` changes
  `org.plan` directly with no payment step — any org owner can self-upgrade to any priced plan for free.
  Worse: `plans.py`'s `enterprise` tier has `price_eur=None` ("contact us"), and the billing-enabled guard
  (`settings.billing_enabled and target.price_eur`) is falsy whenever `price_eur` is `None` — so **even in a
  fully-wired, live-Stripe/EveryPay deployment, any org owner can self-upgrade to the 200-seat Enterprise
  plan for free**, a bypass with zero test coverage that would survive the literal remediation of "just wire
  a live provider."
- **Evidence:** `commercial-readiness.md` §7; strengthened in `agent-debate.md` §9 (the Enterprise
  `price_eur=None` bypass, confirmed reachable from the public, unauthenticated `register` endpoint since a
  fresh org's creator is its `owner` with `BILLING_MANAGE`).
- **Proposed solution:** two parts. (a) **Business decision** (per `commercial-readiness.md` §9 and
  `docs/DECISIONS-NEEDED.md`): decide and document whether a live Stripe/EveryPay key will be wired before
  GA, or whether pilots/early customers are invoiced manually — this is a GTM decision, not purely
  engineering. (b) **Regardless of that decision**, close the Enterprise self-upgrade bypass: require an
  explicit contact-sales/manual-approval gate for any plan with `price_eur=None`, independent of
  `billing_enabled`, so "no listed price" can never mean "free" in `change_plan`'s guard logic.
- **Risk of the fix itself:** low for (b) — it's an additional guard condition, not a removal; needs a
  product decision on what UX replaces "Switch to Enterprise" (e.g. a "Contact sales" CTA instead of a
  self-service switch). (a) carries GTM/business risk to get wrong, not engineering risk — flag for product/
  commercial sign-off before implementation, not a pure work-order item.
  **Compensating control until fixed:** keep `billing_provider` unset in any pilot deployment and do not
  present the Billing page's "Switch to Enterprise" control as a live self-service action.
- **Dependencies:** (a) requires a product/commercial decision before an engineering work order can be
  scoped; (b) can proceed independently and immediately.
- **Acceptance criteria:**
  1. `change_plan`'s guard blocks a self-service switch to any plan with `price_eur is None` unconditionally
     (not gated on `billing_enabled`). — **DONE (WO-31)**
  2. A new test (extending `test_billing_stripe.py`) asserts an org owner cannot self-upgrade to Enterprise
     via `PUT /billing/plan` in both the `billing_enabled=True` and `billing_enabled=False` configurations.
     — **DONE (WO-31):** `test_enterprise_self_upgrade_blocked_billing_disabled` /
     `test_enterprise_self_upgrade_blocked_billing_enabled`.
  3. `docs/architecture/adr/0013-billing-metering.md` is updated to record the resolved GTM decision (wire
     billing before GA vs. manually invoice pilots) and the Enterprise-tier gating fix. — **PARTIAL:** the
     Enterprise-tier gating fix is recorded (WO-31); the GTM decision itself is **not** resolved by this
     work order — it stays open in `docs/DECISIONS-NEEDED.md` item 2, and the ADR says so explicitly rather
     than fabricating a resolution.
  4. `Billing.tsx` UX matches the resolved decision (e.g. "Contact sales" replaces a bare "Switch to
     Enterprise" button when `price_eur is None`). — **DONE (WO-31):** a disabled "Contact sales" control
     replaces the active switch button for any `price_eur === null` plan, independent of the GTM decision.
- **Closed (partial):** `docs/plan/plan-a/wo/WO-31-R5b.md`. Part (b) — the engineering half, items 1/2/4
  above — is fully closed: `backend/app/api/routes/billing.py::change_plan` now raises 409 unconditionally
  for any `price_eur is None` target before the existing `billing_enabled` paid-plan check runs, so the two
  guards can never interact to produce a free self-upgrade; `Billing.tsx` never offers an actionable switch
  for such a plan. Part (a) — the GTM/business decision — remains open, tracked only in
  `docs/DECISIONS-NEEDED.md` item 2 ("Billing go-live"); **R5 as a whole stays open** until (a) is decided.

---

## P2 items — brief detail

### R6 — Reimbursement payout has no maker≠checker (SoD) control — **CLOSED (WO-32)**
Evidence: `functional-audit.md` §2.2. The same `EXPENSE_APPROVE` account that calls `create_batch` can
immediately call `pay_batch` alone, unlike `payment_run`'s explicit `_enforce_sod`. Fix: add an
`_enforce_sod`-style check to `reimbursement.mark_paid` (payer ≠ batch creator), mirroring the payment-run
precedent, or explicitly document the accepted-risk asymmetry in an ADR. Risk of fix: low, follows an
established pattern. Acceptance: a reimbursement batch's creator cannot also be its payer; a test proves it
(mirroring `test_payment_runs_sod.py`).
- **Closed by:** `docs/plan/plan-a/wo/WO-32-R6.md`. `ReimbursementBatch` gained `created_by_id`
  (migration `07a09c738fab`, additive column, no new RLS policy needed); `reimbursement.py` gained
  `_sod_conflict`/`_enforce_sod` (single "creator" role — batches have no separate approval stage),
  called from `mark_paid` before settlement — a conflict raises `AppError` 403 `code="maker_is_checker"`,
  mirroring `payment_run.py::_enforce_sod` exactly, including the audited, explicit platform-admin
  override (`override_sod=true` → `audit.A.REIMBURSE_SOD_OVERRIDE`, never silent). `BatchPay` gained an
  additive `override_sod` field; the route wires `actor_id`/`actor_email`/`is_platform_admin` through.
  Red-then-green: `tests/test_reimbursement_sod.py::test_creator_cannot_mark_batch_paid` and
  `::test_platform_admin_override_is_audited` failed against the pre-fix code (200 instead of 403) and
  pass after. Three pre-existing tests had the batch's creator also pay it (the exact bug closed here) and
  were raised to a second admin-role payer per master-context §9:
  `test_reimbursement.py::test_batch_pay_marks_reports_reimbursed`, `::test_pay_is_version_guarded`, and
  `test_expense_management.py::test_marked_for_reimbursement_report_is_batchable`. Full backend suite:
  1107 passed, 8 skipped (baseline was 1104 passed, 8 skipped — +3 new SoD tests).

### R7 — ClamAV fail-closed malware-scan branch has zero test coverage — **CLOSED (WO-33)**
Evidence: `test-baseline.md` finding #1; debate-adjusted P2 (`agent-debate.md` §7) — control behaves
correctly today (verified live with a monkeypatched `clamd`), this is a regression-prevention gap on an
unreachable-by-default branch. Fix: add tests for the FOUND / non-OK-status / unreachable-scanner branches
using a monkeypatched fake `clamd` module (no real daemon needed — demonstrated feasible in the debate pass).
Risk: none (test-only addition). Acceptance: `scan_malware`'s three configured-scanner branches each have a
dedicated test; CI catches a regression to fail-open.
- **Closed by:** `docs/plan/plan-a/wo/WO-33-R7.md`. Pure test-only change — `app/services/filesec.py` is
  byte-for-byte unchanged (the control was already correct). Added 7 tests to `backend/tests/test_filesec.py`
  via a fake `clamd` module injected into `sys.modules` (no real daemon or package needed):
  `test_clamav_found_rejects_with_signature`, `test_clamav_non_ok_status_rejects`,
  `test_clamav_unreachable_fails_closed` (proves fail-CLOSED), `test_clamav_ok_status_passes`,
  `test_clamav_uses_unix_socket_when_configured`, `test_clamav_uses_network_socket_by_default`, and
  `test_clamav_eicar_short_circuits_before_scanner`. Red-then-green: manually broke `scan_malware`'s
  unreachable-daemon branch (swallow instead of raise) — `test_clamav_unreachable_fails_closed` failed
  ("DID NOT RAISE"); manually broke the FOUND/non-OK branches (no-op instead of raise) —
  `test_clamav_found_rejects_with_signature` and `test_clamav_non_ok_status_rejects` both failed; restored
  the original file (`git diff --stat` confirms zero change) and all tests pass green. Full backend suite:
  1114 passed, 8 skipped (baseline was 1107 passed, 8 skipped — +7 new ClamAV-branch tests, 0 assertions
  weakened). **Correction (WO-34):** the note below originally claimed Milestone B was "closed except for
  R14" at this point — that was wrong. R16 and R18 were still open (no `CLOSED` marker) when this WO
  finished; see their own entries below for accurate status.

### R14 — No application-owned backup/restore tooling exists
Evidence: `test-baseline.md` finding #4. `docs/DECISIONS-NEEDED.md` currently defers this to infrastructure.
This is a **decision-gated** item, not a pure code fix: the board recommends explicitly re-confirming (with
whoever owns production infra for the deployment target) that a tested, documented DR runbook exists
somewhere (even if it's "Postgres managed-service point-in-time-recovery, documented in `docs/DEPLOY-*.md`"),
since the product moves SEPA payment files and holds vendor IBANs. Acceptance: either a documented,
periodically-tested DR runbook exists and is linked from `docs/DECISIONS-NEEDED.md`, or an explicit
application-level backup/restore capability is scoped as a future work order.

**Investigation note (WO-36, re-confirms the characterization above, does not close it):** re-checked
whether a code-actionable slice exists that would not require a product/infra decision.
`docs/DEPLOY-HOSTINGER.md` §"Backups (do this)" already documents a manual `pg_dump`/`tar` backup
procedure, and `docs/architecture/deployment.md` §5 "Backup & recovery" already documents a restore
runbook (provision from PITR/snapshot → repoint `DATABASE_URL` → `alembic upgrade head` →
`integrity.verify_documents` + `/audit/verify` → resume traffic) with an explicit "drill this quarterly"
instruction — so the roadmap's own acceptance bar is *already substantially met in prose*; it is just not
cross-linked from `docs/DECISIONS-NEEDED.md`. What remains genuinely missing is an **automated
restore-drill test** (`pg_dump`/`pg_restore` against a scratch Postgres, then `integrity.verify_documents`
asserting zero corruption) — this is real and code-actionable in principle (`pg_dump`/`pg_restore`/`psql`
are present in this environment), but it is a strictly larger undertaking than a same-day bounded WO: it
needs a second scratch-database lifecycle inside the Postgres CI job, a decision about where the
object-storage bytes for the drill come from, and it still sits on top of the same "what is our actual
production backup story" question the roadmap explicitly defers to infra. Scoped out of WO-36 for that
reason; left as a candidate future work order (add the cross-link from `docs/DECISIONS-NEEDED.md` +
stand up the automated drill test), not selected this round.

### R16 — AR "Issue" screen: destructive actions (Void/Write off) have no confirmation — **CLOSED (WO-34)**
Evidence: `commercial-readiness.md` §5. Unlike Payment Runs (which has `ConfirmDialog`s on re-export and
missing-IBAN acknowledgement), `Issue.tsx`'s `Void`/`Write off` links fire immediately. Fix: add a
`ConfirmDialog` to `Void`/`Write off` (and ideally regroup the ~12-action list into safe-vs-destructive
visual groups), consistent with the pattern already proven in `PaymentRuns.tsx`. Risk: pure frontend UX
change, no backend risk. Acceptance: both actions require an explicit confirm step; existing
`test_issued_*` backend tests unaffected.
- **Closed by:** `docs/plan/plan-a/wo/WO-34-R16.md`. Pure frontend change — `VoidAction` and
  `DisputeActions`' write-off button in `frontend/src/pages/Issue.tsx` now open the shared
  `ConfirmDialog` (`tone="danger"`, explanatory body + optional reason field) instead of firing a bare
  `window.prompt(...)` on click; cancelling issues no request. New
  `frontend/e2e/issue-destructive-actions.spec.ts` (live-app + `page.route` mocking, mirroring
  `upload-duplicate.spec.ts`) proves confirm/cancel/confirm for both actions — both new specs pass, and
  the full 45-spec e2e suite (43 pre-existing + 2 new) passes with zero regressions. The secondary
  "regroup the ~12-action list into safe-vs-destructive visual groups" suggestion was scoped OUT as a
  separate, larger, non-required follow-up (the roadmap's own acceptance bar names only the confirm
  step) — left as backlog, not started. No backend file changed (`backend/app/api/routes/issued.py`'s
  `/void` and `/write-off` routes already validated state transitions and audited the action before this
  WO); `git diff --stat` on the WO's commits confirms only `frontend/**` and `docs/**` touched. One
  backend-suite regression was found and fixed during verification, unrelated to the route logic: the new
  e2e fixture's synthetic issuer VAT number (`EE1########0`, an EE prefix + 9 digits) structurally matched
  `scripts/pii_scan.py`'s EU-VAT-id pattern and correctly failed
  `tests/test_pii_scan.py::test_scan_is_clean_on_the_current_tree`; fixed by nulling the unused field
  (not needed by the test) rather than adding an allowlist entry. Full backend suite: 1114 passed, 8
  skipped, 0 failed — unchanged from baseline (no backend test added or modified). Milestone B remains
  open: R18 has no `CLOSED` marker (not started), and R14 stays explicitly decision-gated (not a code
  fix) — Milestone B is NOT fully closed by this WO.

### R18 — Billing downgrade silently disables modules with no confirmation — **CLOSED (WO-35)**
Evidence: `commercial-readiness.md` §7. Switching Trial→Starter instantly disables Invoice issuing with no
warning (`Billing.tsx` has no `ConfirmDialog` on `choosePlan`, unlike `PaymentRuns.tsx`'s care). Fix: add a
confirmation step listing which modules will be disabled before committing a downgrade. Risk: frontend-only.
Acceptance: a downgrade that would disable ≥1 currently-enabled module requires an explicit confirm listing
which modules are affected.
- **Closed by:** `docs/plan/plan-a/wo/WO-35-R18.md`. Pure frontend change — `Billing.tsx`'s `choosePlan`
  now computes, from data the app already fetches (the existing `useModules()` hook over `GET /modules`,
  diffed against the target `PlanInfo.modules` already present in `GET /billing`'s `available_plans` — no
  backend change needed, no guessed list), which currently-enabled non-core modules the target plan would
  drop. If that set is non-empty it opens the shared `ConfirmDialog` (`tone="danger"`) naming every
  affected module by name before calling `PUT /billing/plan` (or `POST /billing/checkout` on the
  paid-provider path — both reconcile modules identically server-side, so both are gated); cancelling
  issues no request. A plan change that drops nothing currently enabled proceeds exactly as before, with no
  added friction. New `frontend/e2e/billing-downgrade-confirm.spec.ts` (live-app + `page.route` mocking,
  mirroring `issue-destructive-actions.spec.ts`) proves confirm/cancel/confirm for a downgrade that drops
  an enabled module, plus a no-dialog assertion for a plan change that drops nothing — both new specs pass,
  and the full 47-spec e2e suite (45 pre-existing + 2 new) passes with zero regressions. `git diff --stat`
  on the WO's commits confirms only `frontend/**` and `docs/**` touched — `backend/app/**` is byte-for-byte
  unchanged, since `GET /modules`/`GET /billing` already exposed everything the dialog needed. Full backend
  suite: 1114 passed, 8 skipped, 0 failed — unchanged from baseline (no backend test added or modified).
  `python3 scripts/pii_scan.py --tree` clean. **This closes every remaining CODE-fixable item in
  Milestone B** — independently re-verified by grepping every Milestone-B row in this document:
  R4/R6/R7/R16/R18 now carry `CLOSED (WO-##)`, and R5's engineering half, R5(b), was already `CLOSED
  (WO-31)`. Two rows stay open, and both are explicitly business/decision-gated, not engineering gaps: R14
  (backup/restore tooling — decision-gated per its own entry above) and R5(a) (the Stripe/EveryPay-vs-manual-
  invoicing GTM decision, tracked only in `docs/DECISIONS-NEEDED.md` item 2 — R5's own entry states "R5 as a
  whole stays open" until that decision is made). Neither is in scope for this WO or R18's acceptance bar.

---

## P3/P4 items — one-line each (backlog, non-blocking)

- **R8** (P3) — Add `webhooks.assert_public_url()`-style SSRF guard to `oidc.discover()`/`fetch_jwks()`
  — **CLOSED (WO-37)**. `oidc.py` gained `_assert_safe_idp_url()`, which delegates to the existing
  `webhooks.assert_public_url()` (no second implementation) and is called from `discover()` (on the
  constructed `.well-known/openid-configuration` URL) and `fetch_jwks()` (on `jwks_uri`, itself sourced
  from the issuer's own discovery response — a second, attacker-controlled hop) before any `httpx` GET is
  made; a rejection raises `oidc.SsoError`, which the existing routes already turn into a 502
  ("IdP unreachable") / error-redirect, so no route change was needed. New tests in
  `backend/tests/test_sso_oidc.py`: `test_discover_rejects_private_or_reserved_issuer` /
  `test_fetch_jwks_rejects_private_or_reserved_uri` (parametrized over loopback/cloud-metadata/RFC1918/
  IPv6-loopback/`localhost`, asserting **zero** network attempts via a fake `httpx.AsyncClient` that
  records whether `.get()` was ever called), `test_discover_allows_public_issuer` /
  `test_fetch_jwks_allows_public_uri` (a normal public-looking issuer is unaffected), and
  `test_authorize_502s_when_issuer_is_ssrf_unsafe` (end-to-end through the real, unauthenticated
  `GET /auth/sso/{slug}/authorize` route with no monkeypatched `discover`, proving the fix is reachable
  from the actual public route). Every pre-existing `test_sso_oidc.py` test passes unmodified (all
  monkeypatch `discover`/`fetch_jwks` away, so none reaches the new guard). See
  `docs/plan/plan-a/wo/WO-37-R8.md`.
- **R9** (P3) — Extract the 3x-duplicated `_safe`/`_safe_cell` CSV helper to one shared module (pairs with R1)
  — **CLOSED (WO-36)**. WO-29/R1 had already extracted `app/core/csv_safety.py::sanitize_cell` and had
  `erp_export._safe`/`audit_export._safe`/`report_writers._safe_cell` delegate to it, but explicitly left
  a **4th** independent copy out of scope: `backend/app/api/routes/issued.py::_csv_safe`, backing
  `/issued/reports/{summary,receivables,partners,vat}?format=csv` — including `report_partners`'s
  `partner` column, sourced from the unconstrained, buyer-supplied `buyer_name` field, with zero dedicated
  test coverage. WO-36 deletes the local definition; `issued.py` now imports
  `sanitize_cell as _csv_safe` from the shared module (same pattern as the other three), so the entire
  codebase has exactly one implementation of the mitigation
  (`grep -rn '\[:1\] in ("="' backend/app` returns zero hits outside `csv_safety.py` itself). New
  `tests/test_issued_reports.py::test_reports_partners_csv_export_is_formula_injection_safe` proves a
  `=`-leading `buyer_name` lands quote-prefixed in the export and a normal buyer name is untouched — a
  refactor-safety proof (the duplicate was already behaviorally correct, so this is not a red-then-green
  vulnerability fix). `test_reports_csv_export` and the rest of `test_issued_*` pass unmodified. Full
  backend suite: 1115 passed, 8 skipped (baseline 1114 passed, 8 skipped — +1 new formula-injection
  regression test, 0 assertions weakened). R9 is now fully closed; the only code touching this pattern
  anywhere in the app is `app/core/csv_safety.py`.
- **R13** (P3) — Fix `test_fx.py::test_refresh_owner_only_and_graceful` to actually assert non-owner rejection
  — **CLOSED (WO-38)**. The route's `POST /fx/refresh` structural `SETTINGS_MANAGE` gate
  (`app/api/routes/fx.py`) was already correct; only its test coverage was incomplete — the test's sole
  call was always as `auth_client` (the workspace owner), so a denied-role request was never sent despite
  the test's own name/docstring claiming "owner only". Rewritten to use the `role_client` factory (the
  established pattern in `tests/test_authz_routes.py`): `role_client("user_free")` (READ_ONLY, holds no
  `settings.manage`) now asserts `403` with a `detail` field; `role_client("admin")` (ADMINISTRATOR, the
  lowest role the matrix grants `settings.manage` to) asserts `200` and preserves the original
  graceful-failure assertion (`ok in (True, False)` — must not raise even when the ECB host is
  unreachable in the test sandbox). No production code changed — `app/core/authz.py::ROLE_PERMISSIONS`
  confirms `SETTINGS_MANAGE` is held only by `Role.OWNER`/`Role.ADMINISTRATOR`, so the fixed test passes
  against the unchanged route. Full backend suite: 1128 passed, 8 skipped (baseline 1128 passed, 8
  skipped — 0 new tests, 1 existing test rewritten to test what its name claims, 0 assertions weakened).
  See `docs/plan/plan-a/wo/WO-38-R13.md`.
- **R15** (P3) — Stand up a load/concurrency testing harness (none exists today; explicit scope gap, not a
  silent one — see `security-findings.md` §3).
- **R17** (P3) — Add a confirmation step to the Payment Run "Cancel" button, for UX consistency (cancelling
  doesn't move money, so this is cosmetic/consistency, not a control gap).
- **R19** (P3) — Add a guided onboarding/setup-wizard checklist (first vendor, bank connection, invite team).
- **R10** (P4) — Harden `LocalStorage._path`'s `startswith` containment check (not currently reachable; cheap
  defense-in-depth for a future caller).
- **R11** (P4) — Delete the stale `# TODO: secret store` comment on `SsoConnection.client_secret` (sealing is
  already implemented and tested; the comment is simply wrong).
- **R12** (P4) — Delete or clearly banner the stale root `README.md`/`ARCHITECTURE.md` (already disclaimed by
  `00_MASTER_CONTEXT.md`, but a future contributor could still cite them by accident).

---

## Verified controls — no action required

These were raised as findings by the audit board (some at P1 in their original submission) but resolved
through debate as **confirmations of existing strength**, not defects. Recorded here for completeness and to
prevent re-litigation; see `agent-debate.md` for full verification detail.

| Finding | Debate-final label | Note |
|---|---|---|
| Tenant isolation (3-layer: query filter + ORM guard + Postgres RLS w/ FORCE) | P4, no action | Independently reproduced live against a real Postgres cluster twice (Architect + Test Engineer) |
| Structural, CI-gated route authorization (`test_authz_coverage.py`) | P1, no action (retained — systemic-failure bar) | Recommend keeping `test_authz_coverage.py` a required, unfiltered CI gate |
| Upload/attachment security gate (`filesec.py`) covers all 8 intake paths | P2, no action | Live HTTP-level exploit tests (`test_security_hardening.py`) already assert the block |
| ARCH_plan.md's prior risk claims are stale (vendors/partners authz, route-level `_reconcile`, hardcoded EUR) | Informational | Do not cite `ARCH_plan.md`'s risk list as current in future work |
