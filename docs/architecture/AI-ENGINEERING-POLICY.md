# AI Engineering Policy

**Status:** Accepted (reference integration R3, 2026-09-07; origin: the Scrapling
cycle's `AI_POLICY.md` lesson, KT-002) · **Applies to:** code, tests, migrations,
infrastructure, documentation, prompts and agent instructions, generated fixtures
and architecture decisions.

InvoiceIQ is intentionally AI-assisted. AI assistance does not lower the evidence
standard for finance software — it raises it, because the reviewer did not write
the code.

This policy complements ADR-0027, which controls what the *running product* may
send to AI systems. This document controls how AI is used to *develop* the
product. The PR template (`.github/PULL_REQUEST_TEMPLATE.md`) is its enforcement
hook; `engineering-rules.md` §11 links here.

---

## 1. Disclose material AI assistance

For a material change, record AI assistance in a durable place appropriate to the
workflow: the commit trailer / session metadata, the PR description, the audit
ledger (`docs/audit/…/DASHBOARD.md`), or the implementation report. Distinguish
where practical: research/review assistance; generated code; generated tests;
generated documentation; autonomous (agentic) implementation. Trivial spelling and
formatting changes need no separate disclosure.

## 2. Human / Lead Developer understanding is mandatory

A change is not acceptable because an agent says it is correct. The reviewer must
be able to explain: the problem; why the change is required; the invariants it
touches; the transaction / data-flow impact; the new failure modes; the rollback;
and the evidence that validates the result. If nobody can explain the
implementation, it is not maintainable and must not merge.

## 3. Evidence is required

Depending on the change: a regression test reproducing the old defect;
unit/integration/e2e tests; Postgres-only tests; RLS/authorization checks; a query
plan or performance measurement; generated-document parsing; a migration
pre-flight; a visual-regression review; a sandbox/provider reconciliation; a
real-input shadow comparison. "Looks correct" is not evidence. A seeded violation
that turns the new test red is the strongest evidence a test can carry.

## 4. Never weaken a gate to make a change pass

An agent must not solve a failing change by deleting or skipping a test; widening a
performance threshold without evidence; adding a tenant/authz exemption; muting a
vulnerability without an applicability rationale; regenerating a golden or visual
baseline and self-approving it; weakening a DB constraint; turning fail-closed
validation into fail-open behaviour; or removing required audit events. A gate may
change only when the gate itself is proven wrong and the decision and evidence are
recorded.

## 5. AI cannot invent owner / business / legal / tax policy

When behaviour requires a business decision, surface it in
`docs/DECISIONS-NEEDED.md` — subscription grace, seller-of-record VAT, fee
percentages, retention duration, contractual wording, filing eligibility, payout
authorisation, a disclosure channel, branch protection. Engineering implements an
approved rule; it does not manufacture the rule.

## 6. Finance invariants receive explicit review

- **Money:** Decimal/Numeric; currency provenance preserved; no cross-currency sum;
  rounding basis documented and tested.
- **Tenant:** service queries scoped; tenant registry and RLS updated; no
  cross-tenant disclosure.
- **Authorization:** route permission declared; consequential mutations use the
  right capability; UI visibility is never treated as security.
- **Audit:** consequential mutations recorded; failure behaviour per ADR-0012; no
  secrets or PII in audit metadata.
- **Concurrency / idempotency:** the natural operation grain is named; a DB
  constraint, lock or equivalent proves it; retries cannot duplicate money-side
  effects.

## 7. External repository code

Default: **UNDERSTAND → EXTRACT PRINCIPLE → DESIGN FOR INVOICEIQ → IMPLEMENT
NATIVELY.** Before copying a meaningful implementation: verify the licence; record
attribution obligations; review dependency and compatibility impact; review the
security assumptions; decide who maintains it; add tests for InvoiceIQ behaviour.
A permissive licence does not make another project's architecture appropriate;
AGPL and proprietary code are never copied, only their principles
(`docs/reference-investigations/`).

## 8. User / client data and external AI

ADR-0027 remains authoritative. Development agents must not move production or
customer invoices, identifiers, bank data, documents or other protected content
into external AI systems to debug or to create fixtures. Use synthetic, sanitised
or approved test data; repository fixtures must pass the PII quarantine
(`scripts/pii_scan.py`).

## 9. Baselines and generated artifacts

An AI may generate OpenAPI snapshots, diagrams, visual baselines, parser expected
outputs and performance baselines. It cannot be the sole approver of its own new
truth. For a changed safety-relevant baseline: show the semantic diff; explain why
it changed; the relevant specialist or Lead Developer approves; preserve the
evidence. (`ci.yml`'s VR-baseline job is dispatch-only, refuses the default branch and
publishes to the working branch as a reviewable commit, for exactly this reason.)

## 10. High-impact changes require adversarial debate

Record: the proposal; evidence of the current problem; alternatives; complexity;
risk; expected benefit; the adversarial objection; the Lead Developer decision.
Architecture novelty is not a benefit by itself. The reference-integration batches
record this as a per-batch review panel (`docs/plan/REFERENCE-INTEGRATION-PLAN.md` §6).

## 11. Definition of DONE

A task is DONE only when: the implementation exists in the actual repository; the
intended behaviour has tests; the existing relevant tests remain green; migrations,
config and docs are consistent; security / tenant / money invariants were reviewed
where relevant; the architecture review says the complexity is justified; the
rollback is known; the evidence is recorded; and CI is green for the delivered
commit. A recommendation, a local patch or a generated file is not DONE until it
lands and passes the repository gates.

## 12. Contribution note (suggested shape)

> AI-assisted: implementation/test drafting used an AI coding agent.
> Lead review: transaction boundaries, idempotency and rollback validated.
> Evidence: tests X/Y/Z, Postgres gate, CI run N.
> External code copied: none.

The purpose is accountability, not ceremony.
