# DuckDB → InvoiceIQ knowledge-transfer bundle

Reference: `duckdb/duckdb@e3946f2327a3cc622e1ec7fe71d51de49f93e61d`
InvoiceIQ base: `525470a154d9f2b85941daca783f0a3499ec6526`

## Main result
The selected implementation is **not DuckDB**. It is the DuckDB execution principle
applied natively to InvoiceIQ's measured dashboard hotspot:

**filter + current-step selection + SoD + assignee + projection + LIMIT in PostgreSQL,
instead of full ORM hydration + Python reduction.**

Files:
- `docs/reference-repository-learnings.md` — cumulative reference report
- `docs/engineering-pattern-library.md` — cumulative internal playbook
- `patches/duckdb-dashboard-query-pushdown.patch` — selected implementation
- `LEAD-DEVELOPER-ORDERS-DUCKDB.md`
- `VALIDATION-DUCKDB.md`
- `POST-IMPLEMENTATION-REVIEW-DUCKDB.md`

Status: PATCH READY / GITHUB WRITE BLOCKED / NOT DONE.
