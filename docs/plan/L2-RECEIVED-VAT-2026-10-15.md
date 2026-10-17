# L2 — Received-invoice VAT breakdown (2026-10-15)

Implemented locally, pending review/CI; not deployed. Continues the repository's
unblocked L2 task in `docs/BACKLOG.md`. This is a read-only rate summary of saved
line calculations, alongside the existing invoice header and source evidence.

## Contract

Invoice detail and review responses now contain `vat_breakdown`, with sorted
`rates` (rate, net, tax, gross, line count), `complete` and nullable
`matches_header`. Decimal amounts remain JSON strings in the document currency.
Equal numeric rates form one group; zero-rate lines stay visible as their own
group. Sum saved per-line cent rounding: two 0.03 lines at 21% yield 0.02 VAT,
not 0.01 from applying the rate to their combined base.

Missing historical tax/gross snapshots remain null. A missing value makes that
amount unavailable for its entire rate group, without obscuring other complete
groups. Empty invoices are incomplete. Incomplete summaries have unknown header
agreement, not an apparent successful reconciliation. Complete summaries compare
all three totals separately with the saved header. Reads never change financial
values, backfill evidence or approve records.

Both screens show a table and source-review guidance for missing calculations or
header differences. The review screen explicitly describes saved calculations;
unsaved edits are excluded. Existing guarded line-save responses recalculate the
summary, and detail/review agree after refresh. A stale version remains a 409.
The summary uses the same authorized, tenant-scoped invoice lookup as its parent
response; it adds no endpoint or tenant bypass.

No schema migration or dependency was needed. This grouping does not infer
source VAT categories, exemption reasons or reverse-charge treatment. Multiple
source categories with the same percentage are combined in this rate view.
Captured source tax/gross remains separate evidence and cannot substitute for
saved calculations. This is not an accountant export or a tax-filing contract.

## Action description and development prompt

There is no new button. The existing **Save lines** action saves reviewed lines
under the invoice version guard, updates the saved summary and surfaces failures
through the existing review error flow. Source review is the recovery step for
missing or inconsistent evidence; the summary itself cannot overwrite totals.

Regression prompt: "Create a received invoice with repeated 21%, 9% and 0% lines;
verify sorted rate groups in detail and review, exact sums of per-line rounding,
original document currency and header agreement. Save a changed rate and refresh
both views. Reject a stale version without changing the current summary. Remove
a historical calculated tax snapshot and show unavailable tax and incomplete
agreement rather than zero or a reconstruction. Keep captured mismatches distinct
from calculated totals; preserve tenant authorization and approved-record guards."

## Evidence

- 53 focused backend tests passed: new VAT cases, N1 line evidence, invoice CRUD,
  review/approval, OpenAPI snapshot and documentation checks. Nine new VAT cases.
- Six Playwright tests passed: three VAT display/recovery cases and three N1
  line-evidence cases. These use controlled API responses for presentation.
- Additional real SQLite API → Chromium check used actual login, invoice create,
  detail and review with three rates, guarded line correction and browser refresh.
  No business API responses were mocked in that check; synthetic invoice data only.
- Ruff and formatting passed for the seven changed Python modules/test files;
  mypy passed for all 412 app modules. Frontend lint/typecheck and build passed.
- Existing bundle gate passed: 435.7 kB raw / 127.6 kB gzip, limits 460 / 135.
- OpenAPI snapshot regenerated. No new PostgreSQL-specific SQL or schema; no
  fresh PostgreSQL run was performed for this slice. Earlier ECC/financial PG
  evidence belongs to those reports, not this feature.

Rollback: revert the additive response fields, shared summary component and its
two mounts, associated tests, and regenerate OpenAPI. No stored data changed by
this read-only feature needs rollback. Keep all preceding local development work.
