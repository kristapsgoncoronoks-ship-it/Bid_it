# AI Engineering Policy

**Status:** Accepted repository policy from the 2026-09-07 Phase 1 hardening cycle  
**Applies to:** code, tests, migrations, infrastructure, documentation, prompts/agent instructions, generated fixtures and architecture decisions.

InvoiceIQ is intentionally AI-assisted, but AI assistance does not lower the evidence standard for finance software.

This policy complements ADR-0027. ADR-0027 controls what the running product may send to AI systems. This document controls how AI is used to DEVELOP the product.

---

## 1. Disclose material AI assistance

For a material change, record AI assistance in a durable place appropriate to the workflow:

- commit footer/session metadata;
- PR description;
- work-order/audit ledger;
- implementation report.

Distinguish where practical:
- research/review assistance;
- generated code;
- generated tests;
- generated documentation;
- autonomous/agentic implementation.

Trivial spelling/formatting changes do not need separate disclosure.

---

## 2. Human/Lead Developer understanding is mandatory

A change is not acceptable because an agent says it is correct.

The reviewer/Lead Developer must be able to explain:

1. the problem being solved;
2. why change is required;
3. important invariants;
4. transaction/data-flow impact;
5. new failure modes;
6. rollback;
7. evidence validating the result.

If nobody can explain the implementation, it is not maintainable and must not merge.

---

## 3. Evidence is required

Substantial AI-assisted changes require concrete evidence.

Depending on the change:
- regression reproducing the old defect;
- unit/integration/E2E tests;
- Postgres-only tests;
- RLS/authorization checks;
- query plan/performance measurement;
- generated-document parsing;
- migration pre-flight;
- visual-regression review;
- sandbox/provider reconciliation;
- real-input shadow comparison.

"Looks correct" is not evidence.

---

## 4. Never weaken a gate to make a change pass

An agent must not solve a failing change by:

- deleting/skipping a test;
- widening a performance threshold without evidence;
- adding a tenant/authz exemption;
- muting a vulnerability without applicability rationale;
- regenerating a golden/visual baseline and self-approving it;
- weakening a DB constraint;
- turning fail-closed validation into fail-open behavior;
- removing required audit events.

A gate may change only when the gate itself is proven wrong and the decision/evidence is recorded.

---

## 5. AI cannot invent owner/business/legal/tax policy

When behavior requires a business decision, surface it.

Examples:
- subscription grace;
- seller-of-record VAT treatment;
- fee percentages/minimums;
- retention duration;
- contractual wording;
- filing eligibility;
- payout authorization policy.

Engineering implements an approved rule; it does not manufacture the rule.

---

## 6. Finance invariants receive explicit review

### Money
- Decimal/Numeric;
- currency provenance preserved;
- no cross-currency sum;
- rounding basis documented/tested.

### Tenant
- service queries scoped;
- tenant registry/RLS updated;
- no cross-tenant disclosure.

### Authorization
- route permission declared;
- consequential mutation uses correct capability;
- UI visibility is never treated as security.

### Audit
- consequential mutation recorded;
- failure behavior matches ADR-0012;
- no secrets/PII in audit metadata.

### Concurrency/idempotency
- natural operation grain named;
- DB constraint/lock/equivalent proves it;
- retries cannot duplicate money-side effects.

---

## 7. External repository code

Default:

```text
UNDERSTAND
  -> EXTRACT PRINCIPLE
  -> DESIGN FOR INVOICEIQ
  -> IMPLEMENT NATIVELY
```

Before copying a meaningful implementation:
1. verify license;
2. record attribution obligations;
3. review dependency/compatibility impact;
4. review security assumptions;
5. decide who maintains it;
6. add tests for InvoiceIQ behavior.

A permissive license does not make another project's architecture appropriate.

---

## 8. User/client data and external AI

ADR-0027 remains authoritative.

Development agents must not move production/customer invoices, identifiers, bank data, documents or other protected content into external AI/model systems merely to debug or create fixtures.

Use synthetic, explicitly sanitized or approved test data.

Repository fixtures must pass the existing PII quarantine.

---

## 9. Baselines and generated artifacts

An AI may generate OpenAPI snapshots, diagrams, visual baselines, parser expected outputs and performance baselines.

It cannot be the sole approver of its own new truth.

For a changed safety-relevant baseline:
1. show semantic diff;
2. explain why it changed;
3. relevant specialist/Lead Developer approves;
4. preserve evidence.

---

## 10. High-impact changes require adversarial debate

Record:
- proposal;
- evidence of current problem;
- alternatives;
- complexity;
- risk;
- expected benefit;
- adversarial objection;
- Lead Developer decision.

Architecture novelty is not a benefit by itself.

---

## 11. Definition of DONE

A task is DONE only when:

1. implementation exists in the actual repository;
2. intended behavior has tests;
3. existing relevant tests remain green;
4. migrations/config/docs are consistent;
5. security/tenant/money invariants were reviewed where relevant;
6. architecture review says complexity is justified;
7. rollback is known;
8. evidence is recorded;
9. CI is green for the delivered commit when applicable.

A recommendation, local patch or generated file is not DONE until it lands and passes repository gates.

---

## 12. Suggested contribution note

> AI-assisted: implementation/test drafting used an AI coding agent.  
> Lead review: transaction boundaries, idempotency and rollback validated.  
> Evidence: tests X/Y/Z, Postgres gate, CI run N.  
> External code copied: none.

The purpose is accountability, not ceremony.
