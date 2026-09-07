# Paperless-ngx → InvoiceIQ knowledge-transfer bundle

Reference:
- `paperless-ngx/paperless-ngx`
- commit `154a1932d84a26cc741d1dee353096515590a0b3`

InvoiceIQ base:
- `kristapsgoncoronoks-ship-it/Bid_it`
- commit `d30f90b69566b4d9055e597bd824932e25e4daf6`

## Included

### `docs/reference-repository-learnings.md`
Cumulative repository-learning report. The earlier Scrapling analysis is retained and Paperless is appended.

### `docs/engineering-pattern-library.md`
Cumulative internal engineering playbook. Paperless adds PAT-018 through PAT-034 without deleting the Scrapling patterns.

### `patches/combined-reference-runtime-hardening.patch`
Recommended cumulative runtime patch:
- Scrapling lesson: receiver `Retry-After`;
- Paperless lesson: connect-time DNS pinning;
- Paperless lesson: job ID/kind log correlation.

It deliberately keeps InvoiceIQ's existing PostgreSQL durable queue and webhook architecture.

### `patches/pin-github-actions.py`
One-time application helper that replaces the workflow major tags observed on the InvoiceIQ base commit with the exact immutable SHAs resolved during this analysis.

### `BRANCH-PROTECTION.md`
Exact minimum rules recommended for `main`.

### `VALIDATION.md`
What was actually executed versus what remains blocked.

## Recommended order

1. Create `chatgpt/paperless-reference-learnings` from `d30f90b...`.
2. Apply the runtime patch.
3. Run the action-pinning helper and inspect the workflow diff.
4. Add/update the two cumulative docs.
5. Run targeted backend tests, Ruff, format and mypy.
6. Push the branch and run full CI.
7. Configure `main` protection using `BRANCH-PROTECTION.md`.
8. Post-implementation review:
   - Security: DNS pin/SSRF tests.
   - Architect: no new queue/cache/broker.
   - QA: full existing webhooks/jobs/capture suite.
   - Adversarial: remove any abstraction not justified by a real caller.
9. Merge only after CI is green.

## Delivery status

A GitHub branch creation was attempted and returned:
`403 Resource not accessible by integration`.

Therefore this bundle is **commit-ready, not repository-delivered**.
No item requiring repository integration is marked DONE.
