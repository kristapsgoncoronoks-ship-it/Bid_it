# Personal Security Checklist → InvoiceIQ Knowledge Transfer

## Reference

- `lissy93/personal-security-checklist`
- commit `5daa89e74762be7418cd2042e7d82ec738ecb701`
- analyzed 2026-09-06

## InvoiceIQ source of truth

- `kristapsgoncoronoks-ship-it/Bid_it`
- analyzed at `584fb21d4f95ab49f214d592c651252ca645fa1a`

This package is cumulative. It preserves the earlier Scrapling, Paperless-ngx and Twenty learnings and adds the Personal Security Checklist as reference #4.

## What this reference actually changed

### Approved
- P1: pin GitHub Actions to immutable full commit SHAs.
- P1: add a generic credential scan alongside the existing PII scanner.
- P1/P2: lint GitHub workflow security with actionlint + zizmor.
- P2: add PR-time dependency vulnerability review.
- P2: add a canonical, evidence-backed security control register with a deterministic generated view.
- P2: add a PR evidence/risk/rollback/AI-disclosure contract.
- P1 BLOCKED: replace placeholder `SECURITY.md` after a real private reporting channel is enabled and tested.

### Reinforced existing
- privileged MFA and step-up authentication;
- session reauthentication/inactivity policy;
- default-branch protection;
- independent KEK;
- portal capability-token hardening;
- connect-time SSRF hardening.

### Explicitly rejected
- browser localStorage as security assurance;
- title-derived control IDs;
- auto-committing generated docs to the default branch;
- copying the reference's unprotected default-branch governance;
- copying personal-security tips verbatim into server requirements;
- replacing InvoiceIQ architecture with a static Qwik site;
- treating lint/type/build as a substitute for tests;
- copying monthly dependency cadence;
- placeholder APIs/infrastructure.

## Key files

### Cumulative knowledge
- `docs/reference-repository-learnings.md`
- `docs/engineering-pattern-library.md`

### Security control implementation
- `docs/security/security-controls.json`
- `docs/security/SECURITY-CONTROLS.md`
- `scripts/security_control_gate.py`
- `scripts/check_github_action_pins.py`

### Proposed repository changes
- `proposed/.github/workflows/security-supply-chain.yml`
- `proposed/.github/PULL_REQUEST_TEMPLATE.md`
- `proposed/SECURITY.md` — **DO NOT MERGE until owner decision**
- `patches/pin-github-actions.py`

### Decision/review
- `docs/security/VULNERABILITY-DISCLOSURE-DECISION.md`
- `LEAD-DEVELOPER-ORDERS.md`
- `APPLY.md`
- `VALIDATION-PSC.md`
- `POST-IMPLEMENTATION-REVIEW-PSC.md`

### Previous reference-cycle artifacts retained
- Twenty command-palette proposal
- AI action-safety contract
- migration plan-before-apply policy
- combined Scrapling/Paperless runtime hardening
- branch-protection order

## Validation actually executed

- security-control register positive: PASS;
- duplicate ID: refused as expected;
- VERIFIED without evidence: refused as expected;
- open P0: refused as expected;
- proposed workflow YAML: parses;
- proposed workflow external actions: 8/8 immutable SHA pins;
- checkouts: 4/4 credential persistence disabled;
- action pin helper: 24/24 synthetic current-tag replacements across 3 workflow files;
- PR evidence contract: structural PASS;
- Python scripts: compile PASS.

See `VALIDATION-PSC.md` for the execution boundary.

## GitHub delivery

Branch creation from the analyzed InvoiceIQ head was attempted:

`chatgpt/personal-security-checklist-learnings`

GitHub returned:

`403 Resource not accessible by integration`

So this package is **implementation-ready but not repository-delivered**.

Do not call the selected changes DONE until they are applied on a branch and real required CI passes.
