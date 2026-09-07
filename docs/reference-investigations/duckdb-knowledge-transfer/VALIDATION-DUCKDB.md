# DuckDB cycle validation

Reference: `duckdb/duckdb@e3946f2327a3cc622e1ec7fe71d51de49f93e61d`
InvoiceIQ base: `525470a154d9f2b85941daca783f0a3499ec6526`

## Executed successfully

1. Proposed Python fragments parsed using Python `ast`: **PASS**.
2. Final `duckdb-dashboard-query-pushdown.patch` validated with
   `git apply --check` against synthetic files containing the exact inspected
   InvoiceIQ source contexts: **PASS**.
3. Isolated SQLAlchemy relational validation of the proposed set-based query:
   **7/7 PASS**:
   - current lowest pending step selected;
   - later pending step not actionable early;
   - submitter/SoD exclusion;
   - generic NULL approver requires `can_approve_any`;
   - tenant isolation;
   - missing/hidden vendor preserves invoice with `vendor_name=None`;
   - SQL LIMIT applies after semantic filtering;
   - one SELECT for the service path.

(Those bullets contain seven semantic checks plus the one-SELECT observation in
the same validation harness; terminal summary reported 7/7 because the
current-step/SoD pair is one assertion group.)

## Not executed

- InvoiceIQ `pytest` against the patched repository.
- Ruff against the patched repository.
- mypy against the patched repository.
- Real PostgreSQL/RLS suite against the patched repository.
- `alembic check` (no schema change is proposed, but full CI still owns it).
- PERF-018 concurrency harness after the change.
- Full CI/deploy.

## Why

GitHub branch creation for `chatgpt/duckdb-reference-learnings` returned:

`403 Resource not accessible by integration`

The patch therefore has not been applied to the actual repository in this
session.

## Definition of Done

**NOT SATISFIED.**

Required before DONE:
1. apply/rebase patch on current main;
2. `pytest -q tests/test_dashboard.py`;
3. Ruff + format + mypy;
4. full backend suite;
5. real Postgres/RLS CI;
6. rerun dashboard concurrency measurement at scale 300 / concurrency 8;
7. compare against the pre-change ~10.5–11.7× dashboard degradation and ~27 req/s;
8. full CI green;
9. architecture/adversarial review after measurement.
