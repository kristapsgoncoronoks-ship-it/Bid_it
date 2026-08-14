# WO-98 — a retry may not silently destroy a human's corrections

**Priority:** P0 · **Type:** correctness defect in our own code · **Found by:** the
clean-room capability inventory built for the paperless-ngx harvest
(`docs/harvest/CANDIDATES.md`), not by the harvest itself.

## The defect

`POST /invoices/upload/{run_id}/retry` deletes every `ExtractionField` row for
the run so the re-parse can write fresh provenance. Correct for a failed
capture — there is nothing there but a machine's first guess.

Not correct for a capture a human has already reviewed. The `reviewed_value`
rows they typed are deleted with the rest, and the guard did not cover it: it
refused only when `run.invoice_id is not None or run.status == "saved"`, so a
capture that was **parsed AND reviewed BUT NOT YET SAVED** was in scope.

The audit chain records each correction, so the loss was forensically
recoverable. It was silent in the live record, which is the part that matters to
the person who typed them.

It is human-triggered, so it does not violate §4.19 literally — nothing
autonomous overwrote anything. It is still destruction of human work by a
machine action, which is the same failure §4.19 exists to prevent.

## What was NOT done, and why

**Preserving the corrections across the re-parse was rejected.** A re-parse may
use a different provider and produce a different field set, so re-applying an old
correction could attach a human's decision to a field they never saw. That is a
worse failure than losing it, because it *looks* like a decision. Discarding is
the honest behaviour; what was missing is that the human must ask for it.

## The fix

`extraction.clear_fields_for_retry(db, org_id, run, *, discard_review)`:

- counts rows with `reviewed_value IS NOT NULL`;
- raises `ConflictError(code="capture_has_review")` when there are any and
  `discard_review` is false — **before mutating anything**, so a refused retry is
  a true no-op (a guard that rejects after deleting the rows would be worse than
  no guard);
- returns the number discarded so the route can audit it.

The route gains `discard_review: bool = False` and writes a
`capture.review_discarded` audit event with `{"discarded_reviews": n}` when a
discard actually happens. The refusal message names `discard_review` so an
operator can act on it — an error a user cannot act on sends them to support.

The guard lives in the service, not the route: services raise `AppError`, routes
hold no business logic (I-22), and `AppError` is what carries the machine-readable
`code` the SPA can branch on.

## Verification

- 5 new tests in `backend/tests/test_upload_retry_review_guard.py`.
- **3 of them fail against the unfixed code** (verified by stashing the two
  changed files). The 2 that pass are guard rails on behaviour that was already
  correct: an untouched capture still retries without ceremony, and an explicit
  discard still succeeds.
- One test asserts the refusal is a **true no-op** — the corrections are still
  readable through `GET /captures/{id}/fields` afterwards.
- One test pins the audit event, including that `meta` is JSON **text** (the
  chain hashes a string), which my first draft got wrong.
- Lint and format clean.

## Known limits

- The guard keys on `reviewed_value IS NOT NULL`. A human who reviewed a field
  and typed **the same value the machine produced** leaves no `reviewed_value`
  distinct from `value`, so that review is not protected. Detecting it would need
  a separate "reviewed at" marker per field, which does not exist. Recorded
  rather than silently assumed away.
- No frontend change. The SPA does not yet offer the discard affordance, so a
  user who hits the 409 currently cannot proceed from the UI. That is the next
  increment, not a shipped hole — the API is the contract and the CLI/API path
  works today.
