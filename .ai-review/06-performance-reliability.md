# Performance and reliability

No profiling was run. Everything below is analysis of query shape and call frequency, with
the scale at which each concern actually bites stated explicitly — a theoretical cost at
pilot scale is not a defect.

## Findings

### Fixed: one request per keystroke on supplier resolution (F-04)

The capture-review vendor input calls `setDraft` on every change and the resolve query was
keyed off the live value. Each call runs `select(Vendor).limit(2000)` and Python-folds every
returned name. Typing a 20-character supplier name issued ~20 such requests.

Fixed by keying the query off `useDeferredValue`. No debounce utility existed in the
codebase, and `useDeferredValue` is the React-native answer that needs no new dependency.

**Residual:** even debounced, `resolve()` loads up to 2000 vendor rows and folds them in
Python on a cache miss. At a few hundred suppliers this is trivial. At the 2000 cap it is a
full table read per settled edit. The cap is stated in the source rather than hidden, and
correctness never depends on the candidate list being complete — but the honest ceiling for
this implementation is "a few thousand suppliers", after which the near-match search wants to
be a database-side query (trigram index or a normalised-name column).

### Open: the failed-capture worklist is unpaginated (F-05)

`worklist()` loads every failed row for the tenant from two tables and does grouping and
repeat-counting in Python. Its sibling `capture_review_queue` paginates with a 100 cap.

Cost is linear. At the pilot scale this was built for (tens to hundreds of failures) it is
fine and the single round trip is arguably better than paging. The concern is that **load
correlates with the failure being surfaced**: a tenant with a systematically broken supplier
feed accumulates thousands of failures, and that is exactly the tenant who most needs the
page to load. Bites at roughly 10k rows.

Not fixed here: paginating correctly requires moving `groups`/`total` to SQL aggregates so
the header cannot disagree with the paged list, which is a design change rather than a review
edit.

### Open: no N+1, but the acknowledge route does redundant work

`POST .../acknowledge` writes one row then re-runs the whole worklist to return it. That is
deliberate (the caller gets a fresh list without a second round trip) but it means every
acknowledgement pays the full unpaginated cost of F-05. The two issues compound; fixing
pagination fixes both.

No N+1 was found in the worklist itself: acknowledgements are fetched in a single `IN` query
(`_latest_acks`), not per item — which is the shape that would otherwise have been the
obvious mistake here.

### `NEEDS VERIFICATION`: counter drift under concurrent deliveries

`inbound_health.begin_attempt` does read-modify-write on `consecutive_failures` with no
locking. Two simultaneous deliveries for the same org can both read `n` and both write `n+1`,
losing one increment.

**Consequence if real:** only the *transient* alarm threshold (3 consecutive failures) could
under-count and alarm late. A **sticky** failure (auth, module-off) alarms at 1 and is
unaffected, and a success unconditionally resets to 0, so the counter cannot get stuck high.

I did not write a concurrency test and am not asserting this either way. If it matters, the
fix is an atomic `UPDATE ... SET consecutive_failures = consecutive_failures + 1`.

### Reliability: the mid-request commit is sound

`begin_attempt` commits before the document work. I traced the session state at that point:
only the health row is pending (org resolution and the module gate are reads; no
`InboundInvoice` has been added). So the commit flushes exactly one row, and the extra round
trip is one INSERT/UPDATE on a public webhook path — negligible against the parse work that
follows.

This ordering is the change set's best reliability property: a delivery that crashes, times
out or is rolled back halfway leaves the channel recorded as *not succeeded*, rather than
leaving no trace at all. The naive ordering is silent about exactly the deliveries that went
most wrong.

### Memory: attachments decoded up front

`POST /email/inbound` now decodes all attachments into a list before writing any. Peak memory
is **unchanged** — the whole JSON body including every base64 payload was already fully
materialised by the request layer before the handler ran. The per-attachment size cap still
applies. The restructure bought a correctly classified failure with nothing half-written.

## Reliability posture

| Property | State |
|---|---|
| Remote I/O deadlines | Every call site sets a timeout (`oidc` 15s, `mailer` 20s, `billing_provider` 20s, `webhooks`, `fx`). Now enforced by a structural test that fails if a new one is added without one. |
| Failure visibility | Materially improved: failed captures and dead channels now have a surface. |
| Alarm fatigue | Actively defended — transient failures need 3 occurrences, and quiet is never called broken without a stated cadence. |
| Push alerting | Absent. These are screens you visit. A digest is a separate increment. |
