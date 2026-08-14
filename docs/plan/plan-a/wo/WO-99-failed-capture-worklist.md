# WO-99 — H-1, the failed-capture worklist

**Priority:** BUILD NOW (first harvest work order) · **Source:** `docs/harvest/
CANDIDATES.md` §BUILD NOW, drawn from paperless-ngx scouts S1-2, S1-3 and S4-7,
plus the gap A1 confirmed in our own code.

## The hole

`extraction.pending_review_filters` defines the queue of captures that PARSED.
Nothing defined, listed or surfaced the ones that did not. A failed capture was
reachable only through `GET /invoices/upload/{run_id}` — which you can only call
if you already know the run id.

That is the worst shape a document pipeline can take: the customer believes the
document was processed, it was not, and no screen disagrees. An empty review
queue read as "all clear" when it might mean "nothing could be read at all".

## What was built

**A typed outcome contract, not prose.** `services/capture_failures.py` holds a
closed vocabulary (`KINDS`) of eight codes. Each carries a `summary` of what
happened, a `remediation` telling a finance operator what to DO, and two flags
the UI needs and must not guess: `retry_helps` and `user_fixable`. The raw
library message is still kept (`extraction_runs.note` / `inbound_invoices.error`)
and shown behind a disclosure — it is what an engineer needs, and it is never
what the operator is shown as the explanation.

**Classification is type-driven.** `CaptureError(ValueError)` is raised at the
four sites that actually know the cause (unsupported type at provider selection,
OCR unavailable in the PDF and image providers, an unreadable scan). A classifier
that matched words in a message would break silently the day someone reworded
one. A plain `ValueError` is the parse layer's documented bad-input signal →
`malformed_document`; anything else escaped a provider unclassified and is ours →
`internal_error`.

**Both channels.** The worklist unions `extraction_runs` (direct upload) with
`inbound_invoices` (emailed attachment, including security-gate rejections),
because the operator's question — "what did we fail to read?" — does not care how
it arrived. Email is where silent failure hurts most: nobody is watching a
browser tab when it happens.

**What SURVIVED is stated.** Taken from S1-2: a failure record that says only
"failed" is incomplete. `document_retained` says the original is still stored and
re-readable — true in every failure mode except `stored_file_missing`, where
claiming it would produce the wrong advice ("send it again" vs "retry").

**Repeats are grouped.** `repeat_count` keys on the CONTENT hash, not the
filename, so the same invoice re-sent under a new name reads as the same document
coming back. `groups` folds one systemic cause into a single line instead of
forty rows the operator has to infer a pattern from.

**Acknowledgement is a record, not a boolean.** `capture_acknowledgements` is
append-only: who, when, an optional note, and `failure_seen_at` — the timestamp
of the failure the acknowledgement was made against.

## The invariant that needed a test

An acknowledgement covers **the failure it was made against**, not the document
forever. Without the `failure_seen_at` comparison, a capture that is retried and
fails AGAIN inherits the old dismissal and disappears — which is exactly the
silence this worklist exists to break. That is one of the seeded-violation tests.

## What was deliberately NOT done

- **No back-fill of `failure_code` for existing rows.** A failure recorded before
  this contract existed genuinely did not record a classified cause. Deriving one
  by matching words in an old message would manufacture a fact. Those rows read
  as `unknown_failure`, which claims nothing — deliberately *not* as
  `internal_error`, because "we did not record why" and "something broke on our
  side" are different claims and only one of them is true.
- **No retry button where a retry cannot work.** `retry_helps` is part of the
  contract rather than something the screen infers.
- **No alerting/notification.** H-2 (inbound-channel health) is where a *push*
  belongs; a worklist you have to visit is the honest first increment, and the
  Captures page carries a banner so an empty review queue can no longer be
  mistaken for all-clear.

## Verification

- 15 tests in `backend/tests/test_capture_failure_worklist.py`, over the REAL
  pipeline (a genuinely unparseable upload driven through the worker), never a
  hand-written `status = "failed"`.
- **Four seeded violations, all caught**: (1) an old ack silencing a new failure,
  (2) classification removed, (3) `document_retained` hard-coded true, (4) the
  "is this actually a failed capture" guard removed from `acknowledge`. Each was
  applied to the source, the suite was run, the expected test went red, and the
  source was restored.
- A real tenancy probe for `capture_acknowledgements` in
  `test_tenancy_parity.py` — both orgs upload an IDENTICAL unparseable file, so
  only tenancy discriminates. Full parity suite: 73 passed. Deliberately a probe
  rather than an EXEMPT row, in the same commit that creates the table.
- Migration `c7e1a94d5b02`; migration-drift and clean-from-empty guards pass.
- Frontend builds and typechecks clean.

## Known limits

- The email channel has no Retry action: `POST /invoices/upload/{id}/retry` is
  upload-only. An emailed attachment that fails on a transient cause can be
  acknowledged but not re-driven from the UI. Recorded rather than papered over
  with a button that would 404.
- `repeat_count` counts failed captures sharing a content hash. A document that
  failed once and later succeeded does not decrement it — the count describes
  failures, not the document's current state, and the field name says so.
