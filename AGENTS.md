# InvoiceIQ / SME financial workspace

Read docs/architecture/engineering-rules.md and
 docs/architecture/AI-ENGINEERING-POLICY.md before changes. Architecture and
product documentation in docs/ are authoritative.

## Project layout and validation

- backend/: FastAPI, async SQLAlchemy, Alembic; tests in backend/tests/.
- frontend/: React, TypeScript, Vite; browser tests in frontend/e2e/.
- From backend with its dependencies installed: ruff check app tests;
  ruff format --check app tests; mypy app; python -m pytest -q.
- From frontend with its dependencies installed: npm run build.
- Database changes require exactly one Alembic head, upgrade/drift checks,
  and the real PostgreSQL CI gates. A source graph check alone is insufficient.
- Follow .github/PULL_REQUEST_TEMPLATE.md and report only checks actually run.

## Invariants

Preserve tenant-scoped queries, ORM tenancy guards, PostgreSQL RLS, route
permissions, Decimal money/FX provenance, immutable financial records,
append-only audit evidence, and idempotent side effects. Never weaken tests
or controls to obtain a passing result. Keep customer data and secrets out
of external AI tools, logs, fixtures and agent memory.

## Optional Ruflo workflow

See docs/development/ruflo.md. Ruflo is development tooling; it is not an
application dependency or evidence that workers implemented a task.
Use the pinned CLI for explicit analysis and project-local structured memory.
Read current source and relevant ADRs first; verify findings independently.
The basic code scanner does not cover Python. File size is a review prompt,
not proof of a defect. Do not infer security from an empty scanner result.
Use actual host tools for edits and tests. Agent delegation, paid providers,
background workers and remote federation require task authorization.
The optional MCP template changes no model, approval, sandbox or worker defaults.
