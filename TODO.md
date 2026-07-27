# TODO — Task Board

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

---

## P0 — blocks any pilot (Approved)

- [ ] **R2** — `Approved` — Credit-note creation has no row lock, violating the codebase's own
  non-negotiable invariant on over-crediting (reproduced live). Detail:
  `docs/audit/remediation-roadmap.md#r2--credit-note-creation-has-no-row-lock-violating-the-codebases-own-non-negotiable-invariant-on-over-crediting`.
  Evidence: `docs/audit/functional-audit.md` §2.1, `docs/audit/agent-debate.md` §1.
- [ ] **R3** — `Approved` — Demo/seed data self-contradicts: Cash Position/Payment Runs show €0 owed
  while Invoices lists >€1M. Detail:
  `docs/audit/remediation-roadmap.md#r3--demoseed-data-contradicts-itself-cash-positionpayment-runs-show-0-while-invoices-lists-1m`.
  Evidence: `docs/audit/commercial-readiness.md` §3, `docs/audit/agent-debate.md` §8.

## P1 — blocks general (self-serve) release (Approved)

- [ ] **R1** — `Approved` — CSV formula-injection sanitization inconsistently applied across 3
  financial exports (payment-run, reimbursement, analytics explore). Detail:
  `docs/audit/remediation-roadmap.md#r1--csv-formula-injection-sanitization-is-inconsistently-applied-across-financial-exports`.
  Evidence: `docs/audit/functional-audit.md` §3.1, `docs/audit/security-findings.md` §2.4,
  `docs/audit/agent-debate.md` §2.
- [ ] **R4** — `Approved` — Expense-approval decision has no optimistic-concurrency guard or row
  lock; a live UI-reachable `mark_reimbursed` shortcut bypasses the one path that is locked. Detail:
  `docs/audit/remediation-roadmap.md#r4--expense-approval-decision-has-no-optimistic-concurrency-guard-or-row-lock`.
  Evidence: `docs/audit/test-baseline.md` finding #2, `docs/audit/agent-debate.md` §6.
- [ ] **R5** — `Approved` — Self-serve billing collects zero real payment today; Enterprise tier is
  self-upgradable for free even with billing wired (`price_eur=None` bypass). Needs a product/GTM
  decision as part of scoping — see acceptance criteria. Detail:
  `docs/audit/remediation-roadmap.md#r5--self-serve-billing-collects-zero-real-payment-today-enterprise-tier-is-self-upgradable-for-free-even-with-billing-wired`.
  Evidence: `docs/audit/commercial-readiness.md` §7, `docs/audit/agent-debate.md` §9.

## P2 — should fix before general release (Backlog)

- [ ] **R6** — `Backlog` — Reimbursement payout has no maker≠checker (SoD) control, unlike the
  analogous AP payment-run flow. Evidence: `docs/audit/functional-audit.md` §2.2.
- [ ] **R7** — `Backlog` — ClamAV fail-closed malware-scan branch has zero test coverage (control
  behaves correctly today; regression-prevention gap on an unreachable-by-default branch). Evidence:
  `docs/audit/test-baseline.md` finding #1, `docs/audit/agent-debate.md` §7.
- [ ] **R14** — `Backlog` — No application-owned backup/restore tooling exists; decision-gated
  (confirm an infra-level DR runbook exists and is documented, or scope an app-level capability).
  Evidence: `docs/audit/test-baseline.md` finding #4.
- [ ] **R16** — `Backlog` — AR "Issue" screen: destructive actions (Void/Write off) have no
  confirmation dialog, unlike the equivalent Payment Runs pattern. Evidence:
  `docs/audit/commercial-readiness.md` §5.
- [ ] **R18** — `Backlog` — Billing downgrade silently disables modules with no confirmation.
  Evidence: `docs/audit/commercial-readiness.md` §7.

## P3 — backlog / hardening (Backlog)

- [ ] **R8** — `Backlog` — OIDC `discover()`/`fetch_jwks()` has no SSRF guard, unlike the webhook
  delivery path. Evidence: `docs/audit/security-findings.md` §2.5.
- [ ] **R9** — `Backlog` — Duplicate `_safe`/`_safe_cell` CSV-sanitization helper implemented 3x with
  no shared module (pairs naturally with R1). Evidence: `docs/audit/functional-audit.md` §4.1.
- [ ] **R13** — `Backlog` — `test_fx.py::test_refresh_owner_only_and_graceful` doesn't actually test
  "owner only." Evidence: `docs/audit/test-baseline.md` finding #3.
- [ ] **R15** — `Backlog` — No load/concurrency/large-dataset performance testing harness exists.
  Evidence: `docs/audit/system-architecture.md` §3.
- [ ] **R17** — `Backlog` — Payment-run "Cancel" button fires with no confirmation (UX consistency,
  not a control gap — cancelling doesn't move money). Evidence: `docs/audit/commercial-readiness.md` §4.
- [ ] **R19** — `Backlog` — No guided onboarding/setup-wizard checklist for a first-time admin.
  Evidence: `docs/audit/commercial-readiness.md` §2.

## P4 — informational / doc hygiene (Backlog)

- [ ] **R10** — `Backlog` — `LocalStorage._path` containment check uses bare `startswith` (not
  currently reachable; cheap defense-in-depth). Evidence: `docs/audit/security-findings.md` §2.6.
- [ ] **R11** — `Backlog` — Stale `# TODO: secret store` comment on `SsoConnection.client_secret`
  contradicts its own accurate docstring (sealing is implemented and tested). Evidence:
  `docs/audit/security-findings.md` §2.7.
- [ ] **R12** — `Backlog` — Root `README.md`/`ARCHITECTURE.md` are stale (module/route/migration
  counts); already disclaimed by `docs/plan/shared/00_MASTER_CONTEXT.md` but worth banner/removal.
  Evidence: `docs/audit/functional-audit.md` §4.2.

---

## Verified controls — no action required (not tasks; recorded for traceability)

These were raised during the audit (some at P1) but resolved through adversarial debate as
**confirmations of existing strength**, not defects — see `docs/audit/agent-debate.md` and
`docs/audit/remediation-roadmap.md`'s "Verified controls" section:

- Tenant isolation (query filter + ORM guard + Postgres RLS with FORCE) — independently reproduced
  live against a real Postgres cluster twice.
- Structural, CI-gated route authorization (`test_authz_coverage.py`) — recommend keeping this a
  required, unfiltered CI gate.
- Upload/attachment security gate (`filesec.py`) — covers all 8 intake paths, live exploit tests pass.
- `docs/plan/plan-a/ARCH_plan.md`'s prior risk claims (vendors/partners authz, route-level
  `_reconcile`, hardcoded EUR) are stale/false against current source — do not cite that doc as current.

---

*Backlog items predating this audit, if any existed at the repo root, are preserved in
`docs/BACKLOG.md` (pre-existing) — this file is the audit-derived task board and does not duplicate or
supersede that doc; see `docs/BACKLOG.md` for other in-flight work not covered by this audit round.*
