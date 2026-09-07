# Stirling-PDF → InvoiceIQ Lead Developer Orders

Reference: `Stirling-Tools/Stirling-PDF@02b7b17f9fe2a1abaf60c76a7847ad18e98d1261`
InvoiceIQ base: `525470a154d9f2b85941daca783f0a3499ec6526`

## Order 1 — STIR-P1-01 durable lease heartbeat

Apply:
`patches/stirling-job-lease-heartbeat.patch`

Required targeted checks:

```bash
cd backend
python -m pytest -q tests/test_jobs.py
ruff check app/services/jobs.py tests/test_jobs.py
ruff format --check app/services/jobs.py tests/test_jobs.py
mypy app
```

Certification must include real PostgreSQL because Job is tenant-scoped and RLS
must be exercised with the heartbeat's separate session.

Add/run a two-worker regression with an artificially short stale interval:
- worker A owns a handler longer than stale threshold;
- heartbeat renews;
- worker B's reclaim returns zero;
- after A is killed / renewal stops, the same row becomes reclaimable after the
  stale threshold;
- attempts/dead-letter semantics remain unchanged.

Do not change STALE_LEASE_SECONDS merely to make the test pass.

## Order 2 — STIR-P2-01 Tesseract timeout

Apply:
`patches/stirling-ocr-runtime-budget.patch`

Required checks:

```bash
cd backend
python -m pytest -q tests/test_pdf_ocr.py tests/test_capture_failures.py
ruff check \
  app/core/config.py \
  app/services/pdf_ocr.py \
  app/services/extraction_provider.py \
  app/services/capture_failures.py \
  tests/test_pdf_ocr.py
ruff format --check \
  app/core/config.py \
  app/services/pdf_ocr.py \
  app/services/extraction_provider.py \
  app/services/capture_failures.py \
  tests/test_pdf_ocr.py
mypy app
```

Also run the real Tesseract canary in CI/container where the binary is installed.

## Integration order

1. land heartbeat first;
2. land OCR timeout second;
3. full backend;
4. Postgres/RLS job tests;
5. full CI;
6. monitor `processing_timeout` count and job stale-reclaim count after deploy.

Reason: heartbeat fixes queue ownership semantics generally; OCR timeout then
bounds a single native process without forcing a legitimate multi-page job under
the global stale window.

## Explicit non-orders

Do not add Redis/Celery.
Do not add Stirling's ProcessExecutor abstraction.
Do not add a semaphore inside OCR yet.
Do not add ZIP support.
Do not add a generic document pipeline engine.
Do not change file-size caps or OCR page cap in this batch.
