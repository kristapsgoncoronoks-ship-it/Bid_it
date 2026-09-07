# DuckDB → InvoiceIQ Lead Developer Orders

## PERF-DUCK-001 — Dashboard AP inbox query pushdown
Status: PATCH READY / repository write blocked.

Apply `patches/duckdb-dashboard-query-pushdown.patch` to InvoiceIQ main
`525470a154d9f2b85941daca783f0a3499ec6526` (or rebase/readapt if main changed).

Then run, from `backend/`:

```bash
python -m pytest -q tests/test_dashboard.py
ruff check app/services/approval_policy.py tests/test_dashboard.py
ruff format --check app/services/approval_policy.py tests/test_dashboard.py
mypy app
```

Then run the full backend suite and the real-Postgres CI job.

For the actual acceptance measurement:

```bash
DATABASE_URL=postgresql+asyncpg://... python scripts/perf_harness.py --concurrency 8 --scale 300 --rounds 4   --json /tmp/concurrency-after-duckdb.json
```

Compare `dashboard` against recorded datapoints 5–6:
- 10.53× / 11.68× degradation
- about 27 req/s
- zero errors

Do not add a cache or index in the same change. First isolate the effect of
selection/projection/limit pushdown.

## PERF-DUCK-002 — Post-change profiling
If the dashboard remains materially above N=8 after PERF-DUCK-001:
1. capture PostgreSQL EXPLAIN (ANALYZE, BUFFERS) for the inbox query on the perf dataset;
2. identify the dominant remaining dashboard component;
3. measure it independently;
4. only then decide between an index, another projection rewrite, or a small cache.

No second database is approved.
