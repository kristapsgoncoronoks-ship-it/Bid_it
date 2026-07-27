# Changelog

## Unreleased

### Audit
- **4-agent independent SaaS review board audit (Phases 1-11)** ran against branch
  `claude/bidit-invoice-data-analytics` — Lead Product Developer (functional), Lead System Architect
  (architecture/security), Senior Test Engineer (test-quality), and Commercial Director
  (product/commercial readiness), followed by an adversarial debate stage cross-examining every P0/P1
  finding. **No application code was changed in this pass** — read-only investigation, verification
  against the existing (1091-passed/4-skipped) test baseline, targeted re-runs, and — for the hardest
  claims (tenant isolation, numbering/payment concurrency, the credit-note race) — live reproduction
  against a real Postgres 16 cluster stood up for this purpose.
  - 2 findings confirmed at **P0** (credit-note creation missing a row lock — reproduced live as a real
    lost-update over-crediting race; the packaged demo/seed data self-contradicts on payables across
    Cash Position/Payment Runs vs. the Invoices list).
  - 3 findings confirmed at **P1** (CSV formula-injection unprotected on 3 financial exports;
    expense-approval decisions have no optimistic-concurrency/row-lock guard; self-serve billing
    collects no real payment and the Enterprise tier can be self-upgraded for free even with a live
    provider wired).
  - Several additional P1-submitted findings were debate-adjusted down to P2/P4 as **verified
    strengths with no action required** (three-layer tenant isolation incl. live Postgres RLS-FORCE
    proof; structural CI-gated route authorization; the upload/malware-scan gate's full intake-path
    coverage) — recorded for traceability, not as defects.
  - Full reports, the debate transcript, the merged prioritized roadmap, a 9-dimension scorecard, and
    the repository/data-flow reference docs are in `docs/audit/`. The prioritized backlog is tracked in
    `TODO.md` at the repo root. See `docs/audit/remediation-roadmap.md` for the milestone plan (what
    must fix before any pilot vs. before general release) and per-item acceptance criteria feeding the
    next phase of implementation work orders (WO-26+, each a separate, reviewable change).
