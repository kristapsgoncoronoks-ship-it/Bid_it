# Lead Developer Orders — Personal Security Checklist Transfer

Base: `Bid_it@584fb21d4f95ab49f214d592c651252ca645fa1a`

## Order 1 — SEC-SC-001 (P1): immutable GitHub Actions

Use `patches/pin-github-actions.py`, inspect the diff, then add `scripts/check_github_action_pins.py` from this bundle.

Do not change CI job semantics while pinning.

Required:
```bash
python scripts/check_github_action_pins.py
# then actionlint + zizmor + full existing CI
```

## Order 2 — SEC-SC-002 / SEC-SC-003 (P1/P2): supply-chain workflow

Add:
`.github/workflows/security-supply-chain.yml`

It must remain:
- read-only token by default;
- full-SHA pinned;
- additive to PII scan/pip-audit/Dependabot, not a replacement.

## Order 3 — SEC-CTRL-001 (P2): security control register

Add:
- `docs/security/security-controls.json`
- `docs/security/SECURITY-CONTROLS.md`
- `scripts/security_control_gate.py`

Run:
```bash
python scripts/security_control_gate.py --check
```

Register status is evidence, not aspiration.

## Order 4 — ENG-GOV-001 (P2): PR evidence template

Add `.github/PULL_REQUEST_TEMPLATE.md`.

Do not convert the checklist to empty ceremonial checkboxes; reviewers must still inspect actual evidence.

## Order 5 — SEC-GOV-001 (P1): real SECURITY.md

**BLOCKED.**
Owner selects and tests a private vulnerability reporting channel.
Then replace current placeholder policy using `proposed/SECURITY.md` as a structure, removing every placeholder.

## Order 6 — Existing security P1s

Do not allow this repository-hardening work to distract from:
- privileged MFA / sensitive-action step-up;
- session reauthentication/inactivity policy;
- default branch protection;
- mandatory deploy-health assertion;
- production restore/DR certification.

Repository security and application security are separate layers; both matter.
