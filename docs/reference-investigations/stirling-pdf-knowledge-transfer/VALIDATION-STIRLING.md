# Stirling-PDF cycle validation

## Reference
- Stirling-PDF: `02b7b17f9fe2a1abaf60c76a7847ad18e98d1261`
- InvoiceIQ: `525470a154d9f2b85941daca783f0a3499ec6526`

## Executed in this session

### Patch construction
- standalone Python syntax checks for proposed heartbeat/OCR code: **PASS**
- `stirling-ocr-runtime-budget.patch` `git apply --check`
  against exact inspected source contexts: **PASS**
- `stirling-job-lease-heartbeat.patch` `git apply --check`
  against exact inspected source contexts: **PASS**

### Guarded lease semantics
Standalone SQLAlchemy validation:
- owning worker can renew: PASS
- wrong worker cannot renew: PASS
- renewal moves lease forward: PASS
- renewed live job is not stale: PASS
- completed job cannot be resurrected: PASS

Result: **5/5 PASS**.

### Pytesseract capability verification
InvoiceIQ pins pytesseract 0.3.13. Its documented API supports `timeout=` on
`image_to_string` and `image_to_data`; expiry terminates Tesseract and raises
RuntimeError. The patch deliberately catches `TesseractError` first because
normal Tesseract process errors must not be mislabeled as timeout.

## Not executed

- patch applied to the real InvoiceIQ Git tree;
- actual `pytest tests/test_jobs.py`;
- actual `pytest tests/test_pdf_ocr.py`;
- Ruff;
- mypy;
- real PostgreSQL/RLS heartbeat test;
- real Tesseract canary after patch;
- full backend suite;
- frontend suite (no frontend change expected);
- full CI/deploy.

## Blocker

Attempted branch:
`chatgpt/stirling-pdf-reference-learnings`

GitHub response:
`403 Resource not accessible by integration`

The connector reports pull access but no push permission.

## Definition of Done

**NOT SATISFIED** until the patches land on a branch, actual repository tests
pass, real PostgreSQL proves heartbeat/RLS behavior, real Tesseract canary passes,
and full CI is green.
