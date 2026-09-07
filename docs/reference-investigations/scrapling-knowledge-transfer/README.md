# Scrapling → InvoiceIQ Knowledge-Transfer Bundle

**Reference:** D4Vinci/Scrapling  
**InvoiceIQ base:** `d30f90b69566b4d9055e597bd824932e25e4daf6`

## Contents

- `docs/reference-repository-learnings.md`
  - architecture;
  - execution flow;
  - 18 engineering lessons;
  - comparisons;
  - rejection register;
  - debates;
  - prioritized backlog;
  - explicit Lead Developer orders.

- `docs/engineering-pattern-library.md`
  - cumulative internal architecture playbook;
  - adopted/conditional/rejected patterns;
  - designed to be appended after later reference-repository studies.

- `docs/AI-ENGINEERING-POLICY.md`
  - AI contribution provenance;
  - evidence rules;
  - finance/tenant/audit/concurrency invariant review;
  - no gate weakening;
  - no invented business/legal/tax policy;
  - external-code/license rules.

- `scrapling-retry-after.patch`
  - code/test patch adapting Scrapling's receiver-directed backpressure lesson to InvoiceIQ's existing durable job queue.

- `VALIDATION.md`
  - exactly what was tested;
  - what remains blocked;
  - required repository validation.

## Recommended application order

1. Create branch from `d30f90b...`:
   `chatgpt/scrapling-reference-learnings`
2. Copy the three `docs/` files into the repository.
3. Apply `scrapling-retry-after.patch`.
4. Run targeted backend tests/lint/type checks.
5. Run full CI.
6. Have System Architect + Security + QA + Adversarial reviewers inspect the diff.
7. Merge only after green CI.

## Important

The connected GitHub integration returned 403 on branch creation. These files are therefore **commit-ready artifacts**, not a claim that the repository was modified.
