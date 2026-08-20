# Changelog

## Unreleased

### Added (2026-08 build-out, post-audit)

- **Deletion chain, recycle bin and the platform archive** — deleting an invoice
  goes to a 30-day bin, then a sealed 3-year archive; server-enforced versioned
  consent for anything past draft; reference-counted purge at expiry with
  pre-expiry notices and a paid retention extension. Designs:
  `docs/design/deletion-and-archive.md`, `docs/design/platform-archive.md`.
- **Project lifecycle & profitability (phases 1–5a)** — per-project P&L with
  cent-exact supplier-invoice allocation, expense links and manual cost entries;
  close-freeze with labelled after-close adjustments; versioned offers/estimates
  with org-configurable numbering; invoicing plans tracked against
  actually-issued; **dynamic document templates** (operator masters, frozen
  per-workspace versions, visible-gap rendering, PDF generation into the
  project's documents). Design: `docs/design/project-profitability.md`.
- **Plan ladder** — Free €0 · Starter €39 · Team €99 (750/mo cap) · Business
  €249 · Enterprise · Practice.
- **User manual** at `docs/MANUAL.md`; architecture set trued up 2026-08-20
  (94 tables / 104 migrations / 87 tenant-guarded models).
- License made machine-readable: SPDX `GPL-3.0-or-later` declared in
  `backend/pyproject.toml` and `frontend/package.json` (LICENSE was already
  GPLv3).

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
