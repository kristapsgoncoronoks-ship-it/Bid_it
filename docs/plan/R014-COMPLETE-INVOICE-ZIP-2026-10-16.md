# R014 / INV18 / E16-02 — Complete-or-refused invoice PDF ZIP

AI-assisted implementation and evidence drafting; pending exact-head CI and human
review. Source: the two uploaded October 16 reviews, reconciled in
[the review register](../reviews/RECONCILIATION-2026-10-16.md).

The original route reproduced two independent failures: 501 matches returned a
silent 500-document archive; a paid match beyond the first 500 rows returned 404.
The replacement applies tenant/date/canonical status filters before a deterministic
(issue date, ID) 501-row overflow check. Draft/approved/unnumbered records remain
excluded. Zero matches returns 404; over 500 returns 422 with narrowing guidance.
Dates/status validate before rendering. It creates every selected PDF or refuses
the entire response; a missing/failed source returns a generic 503.

A manifest identifies workspace, all-issuer scope, selection/status dates, filters,
record timestamps, IDs, original numbers/currencies, exact PDF byte counts and
SHA-256 hashes. Sanitized filenames include immutable IDs to prevent collisions.
The manifest describes the captured selection, not a future-state guarantee.
Source PDF bytes and final archive each have a 32 MiB budget; overflow refuses the
whole download. Compression runs outside the event loop. This remains a bounded
synchronous export, not a durable archive service or a modest-hardware benchmark.

Before releasing bytes the endpoint ends its read snapshot, reloads authenticated
identity, organization/module and read permission, and refuses offboarding or a
workspace change. There is no new business mutation or database migration.
The UI names ZIP-specific filters, describes all-issuer scope/limits, prevents
repeat clicks and inverted dates, explains failures and permits safe read retries.
It says the archive is prepared; it does not claim the browser saved a file.
Reminder scope remains separately described as all overdue invoices.

## Verification

* 10 focused backend tests passed: 0/1/500/501, late status match, date/status
  validation, full manifest/hash accounting, filename collisions, unavailable PDF,
  byte overflow and access revoked during rendering.
* 67 related status/invoice/payment/report regressions passed, including canonical
  Python/SQL status parity. This overlaps the focused parity test; not additive.
* Real migrated non-superuser PostgreSQL test passed with real rendered PDF bytes,
  another tenant's empty selection, and revocation committed in an independent
  session before response release. Registered in required CI; registration checks
  passed (three tests including the PG test).
* Four affected browser tests passed, including pending/overflow/retry and invalid
  dates. These mock response branches; they do not prove real ZIP contents.
* Separately, unmocked Chromium → Vite → FastAPI → PostgreSQL downloaded a real
  three-PDF archive: **9,061 bytes**, byte-for-byte equal to the HTTP response,
  SHA-256 `0a1b8dbe8a54295f81f69ca22d8d29f4345f42531b1bd49246b1a68dcceaae4a`.
  Every saved PDF matched its manifest length/hash; no browser errors. An initial
  live probe reached a stale API process and was discarded; the current-process
  run above is the credited evidence. Synthetic records only, no provider send.
* Ruff/format, mypy (416 files), frontend lint/build passed. No assertion or budget
  was weakened. Full branch CI and broader release/security gates remain required.

## Action contract and maintenance prompt

Download PDFs (ZIP): use the displayed period/status across the current workspace's
issuers. Receive a complete bounded archive plus manifest, or a visible failure
with narrowing/access/retry guidance. While pending, block duplicate submissions.

Engineering prompt: preserve one canonical display/SQL status decision list and
all-before-limit filtering. Cover 0/1/500/501, source failure, permission changes,
byte caps and real browser-saved contents. Never truncate, conceal missing PDFs or
claim successful saving merely because a request started. Rebuild paired API/UI
for rollout; rollback the paired implementation if necessary, retaining the old
export defect as a release blocker rather than certifying old archives complete.
