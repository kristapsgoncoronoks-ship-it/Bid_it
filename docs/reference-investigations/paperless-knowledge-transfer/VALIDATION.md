# Validation report

Base repository commit: `d30f90b69566b4d9055e597bd824932e25e4daf6`

## Executed isolated checks

Passed: **19/19**

Validated:
- public vs private/link-local/CGNAT/NAT64 address classification;
- request host rewrite to a vetted IP;
- original Host header preservation;
- original TLS SNI preservation;
- private connect-time DNS rebinding is refused;
- numeric and HTTP-date Retry-After parsing;
- invalid/non-finite Retry-After ignored;
- downstream hint never shortens local queue backoff;
- job logging ContextVars set/reset correctly.

## Not executed

The full InvoiceIQ repository could not be materialized/written from this session because:
- GitHub branch creation returned HTTP 403 (`Resource not accessible by integration`);
- therefore no repository branch exists on which to run the complete pytest/Ruff/mypy/CI suite.

## Required after applying

```bash
cd backend
python -m pytest -q tests/test_outbound_http.py tests/test_webhooks.py tests/test_jobs.py
ruff check app tests
ruff format --check app tests
mypy app
cd ..
python patches/pin-github-actions.py   # or copy it outside repo and run once
git diff --check
```

Then push a working branch and require the full existing GitHub CI.

Status: **PATCH VALIDATED IN ISOLATION; REPOSITORY DELIVERY/CI BLOCKED; NOT DONE.**
