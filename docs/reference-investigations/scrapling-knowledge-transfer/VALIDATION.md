# Validation Report — Scrapling Knowledge Transfer

**InvoiceIQ base:** `d30f90b69566b4d9055e597bd824932e25e4daf6`  
**Date:** 2026-09-06

## GitHub delivery attempt

Attempted branch:
`chatgpt/scrapling-reference-learnings`

Result:
`403 Resource not accessible by integration`

The repository itself reports admin/push permission, but the installed GitHub App connection does not expose write permission for Git refs in this session.

A direct `git clone` fallback was also attempted in the execution container and failed because that environment cannot resolve `github.com`.

Therefore:
- no repository file is claimed as changed;
- no PR is claimed as opened;
- no patch is marked DONE.

## Local logic validation executed

The core new behavior was isolated and executed with Python standard-library semantics matching the patch.

Assertions passed: **9**

1. `"120"` Retry-After -> 120 seconds.
2. `"-5"` -> clamped to zero.
3. HTTP date 90 seconds in future -> 90 seconds.
4. garbage value -> ignored.
5. `NaN` -> ignored.
6. `Infinity` -> ignored.
7. attempt-1 local backoff 30s + receiver hint 5s -> **30s**.
8. attempt-1 local backoff 30s + receiver hint 120s -> **120s**.
9. attempt-3 local backoff 120s + receiver hint 20s -> **120s**.

This proves the intended pure rule:

```text
effective retry = max(local queue backoff, valid receiver hint)
```

## Repository validation required after applying patch

Run:

```bash
cd backend
python -m pytest -q tests/test_jobs.py tests/test_webhooks.py
ruff check app tests
ruff format --check app tests
mypy app
```

Then run full CI, including:
- SQLite/full backend suite;
- Postgres migrations/RLS/concurrency;
- performance-shape gate;
- frontend build/E2E/visual regression;
- Docker builds;
- PII scan.

## Acceptance status

- Reference analysis: COMPLETE
- Comparative architecture review: COMPLETE
- Adversarial debate: COMPLETE
- Lead Developer orders: COMPLETE
- Knowledge documents: PREPARED
- Retry-After implementation: PATCH PREPARED
- Isolated behavior validation: PASS
- Repository integration tests: BLOCKED
- Repository commit/PR: BLOCKED
- DONE: **NO — correctly withheld until patch lands and CI is green**
