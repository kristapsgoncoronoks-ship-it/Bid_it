# Main Branch Protection

Status: repository setting, not source code.

Last checked: 2026-09-07. GitHub reports `main` as `protected: false`, with no repository rulesets configured. The Codex GitHub connector can read this state but does not expose a branch-protection write operation.

The `main` branch should be protected before Phase 1 is considered complete in production.

## Required Rules

- Require pull request before merge.
- Require the branch to be up to date before merge.
- Block force pushes.
- Block branch deletion.
- Keep emergency bypass limited to repository administrators.
- Do not require a second human approval while this is a single-maintainer repository.

## Required Checks

Require these checks on `main`:

- `workflow-security`
- `pii-scan`
- `lint`
- `backend`
- `postgres`
- `frontend`
- `frontend-e2e`
- `docker-build`

Do not require the conditional `deploy` job. It runs only after a push to `main` and can be intentionally skipped while deployment is disabled.

## Bypass Rule

Emergency bypass is for production recovery only. Follow any bypass with a normal PR that documents what happened, why the bypass was necessary, and how the repository state was reconciled.
