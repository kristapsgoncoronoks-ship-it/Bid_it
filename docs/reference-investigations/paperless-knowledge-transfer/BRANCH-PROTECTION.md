# InvoiceIQ main-branch protection order

Reference lesson: paperless-ngx protects its development branch with required CI/security checks.
Current observed InvoiceIQ state at `d30f90b...`: `main` reports `protected: false`.

## Minimum ruleset

Target: `main`

Require:
- pull request before merging;
- branch must be up to date before merge;
- status checks:
  - `pii-scan`
  - `lint`
  - `backend`
  - `postgres`
  - `frontend`
  - `frontend-e2e`
  - `docker-build`
- block force pushes;
- block branch deletion.

For a single-maintainer repository, do not require a second-human approval yet if that makes all merges impossible. Add 1+ required approvals as soon as another qualified reviewer exists.

Emergency bypass:
- repository administrators only;
- use only for production incident recovery;
- follow immediately with a normal PR that documents/reconciles the bypass.

Do NOT require the conditional `deploy` job as a merge check; it runs only after a push to main and can be intentionally skipped when deployment is disabled.
