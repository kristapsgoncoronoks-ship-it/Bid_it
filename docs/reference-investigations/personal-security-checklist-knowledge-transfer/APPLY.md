# Apply This Reference Cycle to InvoiceIQ

Reference base inspected: `584fb21d4f95ab49f214d592c651252ca645fa1a`.

If `main` has moved, re-read the workflows before applying. Never overwrite newer security/deployment changes blindly.

## 1. Copy the security-control register

From this bundle into the repository:

```text
docs/security/security-controls.json
docs/security/SECURITY-CONTROLS.md
scripts/security_control_gate.py
scripts/check_github_action_pins.py
```

Run:

```bash
python scripts/security_control_gate.py --check
```

## 2. Pin the current workflow actions

From repository root:

```bash
python patches/pin-github-actions.py
git diff -- .github/workflows
python scripts/check_github_action_pins.py
```

Inspect every SHA/version comment. The helper is a mechanical transformation, not approval.

## 3. Add supply-chain CI

Copy:

```text
proposed/.github/workflows/security-supply-chain.yml
```

to:

```text
.github/workflows/security-supply-chain.yml
```

Before making it required, verify that dependency review is available for the repository and run the workflow on the branch.

## 4. Add PR review contract

Copy:

```text
proposed/.github/PULL_REQUEST_TEMPLATE.md
```

to:

```text
.github/PULL_REQUEST_TEMPLATE.md
```

## 5. Do NOT merge the proposed SECURITY.md yet

Read:

```text
docs/security/VULNERABILITY-DISCLOSURE-DECISION.md
```

Owner/admin chooses and tests the real private reporting route. Only then remove the explicit placeholders from:

```text
proposed/SECURITY.md
```

and replace the current template.

## 6. Required verification

At minimum:

```bash
python scripts/check_github_action_pins.py
python scripts/security_control_gate.py --check
```

Then require the new workflow itself to pass:
- actionlint;
- zizmor;
- TruffleHog;
- dependency review.

Finally run all existing InvoiceIQ required CI jobs. Do not mark DONE from these bundle validations alone.
