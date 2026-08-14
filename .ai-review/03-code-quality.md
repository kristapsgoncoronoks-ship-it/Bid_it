# Findings

Every finding cites file:line and quotes evidence. Findings marked `NEEDS VERIFICATION` are
suspicions I could not prove and have not asserted as facts.

---

## F-01 — `mypy app` was red; the branch could not pass CI

- **Severity:** P1 · **Confidence:** high · **Category:** correctness / CI
- **File:** `backend/app/services/invoice_pdf.py:454` (pre-fix)
- **Introduced by:** `b6d12db` "fix(invoice): a credit note may not ask to be paid" — earlier
  on this branch, **not** in the reviewed change set.
- **Evidence:**

  ```
  app/services/invoice_pdf.py:465: error: Incompatible types in assignment
  (expression has type "tuple[str, str, str]", variable has type "tuple[str, str]") [assignment]
  Found 1 error in 1 file (checked 333 source files)
  ```

  The credit branch assigns `seller_keys = ("email", "notes")` and the payment branch assigns
  `seller_keys = ("payment_instructions", "email", "notes")`. mypy fixes the type from the
  first branch it sees and rejects the second.
- **Impact:** `.github/workflows/ci.yml` runs `mypy app` as a gate. The branch would have
  failed CI the moment the runners came back. Nobody would have found this until then,
  because the failing gate is one I never ran while building.
- **Resolution:** **FIXED.** Declared `seller_keys: tuple[str, ...]` before the branch.
- **Verification:** `mypy app` → `Success: no issues found in 333 source files`.
- **Status:** FIXED

---

## F-02 — this change set broke `e2e/nav.spec.ts`

- **Severity:** P1 · **Confidence:** high (reproduced and re-verified) · **Category:** regression
- **File:** `frontend/src/lib/nav.ts:56` (pre-fix) → asserted at `frontend/e2e/nav.spec.ts:125`
- **Evidence:** running the spec produced

  ```
  waiting for getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Documents' })
    14 × locator resolved to 1 element - unexpected value "1"
  > 125 |     await expect(nav.getByRole("link", { name: label })).toHaveCount(0);
  1 failed [chromium] › nav.spec.ts:110 › base user role hides admin/owner-gated items
  ```

  Line 125 matches by accessible name **without** `exact: true` (unlike line 150, which uses
  it). The new nav label `"Unread documents"` therefore substring-matched the admin-gated
  `"Documents"` item the test asserts is hidden from a base user.
- **Impact:** CI e2e failure. More importantly the test was *right*: two nav items where one
  label contains the other read as parent/child in the sidebar, and "unread" wrongly suggests
  an inbox state rather than "the machine could not read this".
- **Resolution:** **FIXED** by renaming the nav label to `"Failed captures"` — which also
  pairs correctly with the "Captures" item directly above it. The test was **not** modified;
  weakening a correct assertion to accommodate a weak label would have been the wrong fix.
- **Verification:** `npx playwright test e2e/nav.spec.ts` → `9 passed`; full CI e2e set
  `npm run test:e2e` → `270 passed`.
- **Status:** FIXED

---

## F-03 — the two inbound webhook routes handled tenant scoping differently

- **Severity:** P2 · **Confidence:** high · **Category:** architecture / defense-in-depth
- **File:** `backend/app/api/routes/email.py` — mailgun route (pre-fix, ~line 218)
- **Evidence:** in `POST /email/inbound` the module gate and both health writes run inside
  `scope = set_current_org(org_id)`. In `POST /email/inbound/mailgun` they ran **before** it:

  ```python
  if not await modules.is_enabled(db, org_id, "email_intake"):
      await inbound_health.record_failure(...)      # <- outside the tenant scope
  await inbound_health.begin_attempt(...)           # <- outside the tenant scope
  ...
  scope = set_current_org(org_id)
  ```
- **Impact:** **Not a leak.** Both statements filter `org_id` explicitly and the row is
  constructed with an explicit `org_id`, so the data written is correct. What was missing is
  the *second* of this repo's three isolation layers — the `do_orm_execute` guard in
  `app/core/tenant.py`, which is precisely the belt for a query that forgets its filter. The
  larger cost is the asymmetry itself: two sibling routes doing the same thing differently is
  how the next person copies the wrong one.
- **Resolution:** **FIXED** — the gate and both writes moved inside the scope, mirroring the
  generic route, with a comment recording why.
- **Verification:** `ruff check` + `mypy app` clean; email-intake and Mailgun suites re-run.
- **Status:** FIXED

---

## F-04 — supplier resolution fired one request per keystroke

- **Severity:** P2 · **Confidence:** high · **Category:** performance
- **File:** `frontend/src/pages/CaptureReview.tsx:262` (pre-fix), input at `:622`
- **Evidence:** the vendor header input calls `setDraft` on every change:

  ```tsx
  onChange={(e) => setDraft({ ...draft, [h.key]: e.target.value === "" ? null : e.target.value } as InvoiceCreate)}
  ```

  and the resolve query was keyed directly off that live value:

  ```tsx
  queryKey: ["vendors", "resolve", draft?.vendor_name ?? ""],
  ```

  Server-side, each call runs `select(Vendor).limit(2000)` and Python-folds every returned
  name (`backend/app/services/vendor_resolution.py:206`, `_CANDIDATE_SCAN_LIMIT = 2000`).
  No debounce utility exists anywhere in the codebase (`grep -rn "debounce|useDeferredValue"
  src/` → no matches).
- **Impact:** typing a 20-character supplier name issued ~20 requests, each scanning up to
  2000 vendor rows. Wasteful at any scale; visibly bad for a large supplier master.
- **Resolution:** **FIXED** — the query is keyed off `useDeferredValue(draft?.vendor_name)`.
  The answer is only useful once the user stops typing.
- **Verification:** `npx tsc --noEmit` clean; `npm run build` succeeds.
- **Status:** FIXED

---

## F-05 — the failed-capture worklist is unpaginated while its sibling is not

- **Severity:** P2 · **Confidence:** high · **Category:** performance / consistency
- **File:** `backend/app/services/capture_failures.py` (`worklist`), vs
  `backend/app/api/routes/invoices.py:422` (`capture_review_queue`, which takes `page` /
  `page_size` and caps at 100).
- **Evidence:** `worklist()` selects **all** failed `extraction_runs` and **all**
  failed/rejected `inbound_invoices` for the tenant, then computes repeat counts and grouping
  in Python. `grep -n "page|limit|offset" app/services/capture_failures.py` returns no
  pagination.
- **Impact:** linear growth in memory and response size. A tenant with a systematically broken
  supplier feed is exactly the tenant most likely to accumulate thousands of failures — i.e.
  the load correlates with the failure this feature exists to surface. At pilot scale
  (hundreds) this is fine.
- **Recommendation:** paginate the items and compute `groups`/`total` with SQL aggregates so
  the header stays truthful across pages. Mirror the sibling's `page`/`page_size` contract.
- **Resolution:** **FIXED.** The two sources are now unioned in SQL and the total, the
  per-code grouping and the page all come from that one definition, so the header describes
  the whole filtered set while the items are the page. Enrichment (acknowledgement details,
  repeat counts) is bounded to the page. `bulk_acknowledge` no longer materialises the whole
  worklist to ask about twenty ids — it looks up those ids (`items_for_refs`).
- **Status:** FIXED

---

## F-06 — `updated_at` is used as a proxy for "when did this fail"

- **Severity:** P3 · **Confidence:** high · **Category:** maintainability / latent correctness
- **File:** `backend/app/services/capture_failures.py` (`_item`, `_superseded`, `_failed_at`)
- **Evidence:** the acknowledgement-supersession rule compares `ack.failure_seen_at` against
  the row's generic `updated_at`, set by `TimestampMixin.onupdate` on **any** mutation.
- **Verification performed:** I traced every writer of `extraction_runs` and
  `inbound_invoices`. Today, each one either sets `status="failed"` (a genuine new failure —
  correctly resurfaces the item) or moves the row out of the failed state entirely
  (`parsed`, `saved`, `pending`, `confirmed`, `discarded`), removing it from the worklist.
  **So the behaviour is correct today.**
- **Impact:** it is correct by coincidence of the current write set, not by construction. The
  day someone adds a field to either table and updates it in place on a failed row, every
  acknowledged failure silently resurfaces.
- **Recommendation:** ~~an explicit `failed_at` column~~ — **stronger, after new evidence
  below: a monotonic per-record FAILURE COUNTER.** Any wall-clock column, however explicit,
  still collides at its own resolution; an integer does not.
- **NEW EVIDENCE (2026-08-14): this produced an observed flaky test.**
  `test_a_capture_that_fails_again_returns_to_the_worklist` failed once during the F-05 work
  and did not reproduce in 8 subsequent runs (5 of the file alone, 3 of the three-file group).
  The mechanism is consistent with the rarity: coverage is `ack.failure_seen_at >=
  failed_at`, so a re-failure recorded in the SAME timestamp tick as the acknowledgement is
  wrongly treated as already covered and stays hidden. In tests the acknowledge → retry →
  re-fail sequence can complete inside one tick; in production a human acknowledges and a
  retry follows seconds later, which is why this is rare rather than routine.
  **This is not caused by the SQL rewrite** — the Python rule had identical semantics
  (`failed_at > seen` → not covered, equality → covered). The rewrite only changed where it
  is evaluated.
  I am NOT claiming a fix and NOT claiming the flake is resolved: it was observed once and
  has not been reproduced.
- **Status:** OPEN — promoted. This is now the highest-value open finding: it is a silent
  wrong-answer (a real failure stays hidden), and it has been seen once for real.

---

## F-07 — the email channel of the worklist has no test

- **Severity:** P2 · **Confidence:** high · **Category:** test coverage
- **File:** `backend/tests/test_capture_failure_worklist.py`
- **Evidence:** `grep -n "email" backend/tests/test_capture_failure_worklist.py` returns
  **nothing**. Every test drives the `upload` channel. The `inbound_invoices` branch of
  `worklist()`, the `SECURITY_REJECTED` default for `status == "rejected"`, and
  `acknowledge(channel="email", ...)` are all unexercised.
- **Impact:** email is the channel where silent failure hurts most — nobody is watching a
  browser tab when an emailed invoice fails. It is the half of the feature with the weakest
  evidence behind it.
- **Recommendation:** one test posting a hostile attachment through `POST /email/inbound`,
  asserting it appears on the worklist with `channel == "email"` and code
  `security_rejected`, and that acknowledging it via the email channel works.
- **Resolution:** **FIXED** — `backend/tests/test_capture_failure_email_channel.py` does
  exactly that, plus a test that BOTH channels appear on one worklist (each half looks
  healthy alone if the union breaks, so it is asserted explicitly). Proven non-vacuous by
  seeding: dropping the `inbound_invoices` half of the union turns all three red.
- **Status:** FIXED

---

## F-08 — no authorization test on the new routes

- **Severity:** P2 · **Confidence:** high · **Category:** test coverage / security
- **Files:** `backend/tests/test_capture_failure_worklist.py`,
  `test_vendor_resolution_provenance.py`
- **Evidence:** the only status-code-403 assertion across all three new test files is
  `test_inbound_channel_health.py:140`, and that asserts a *module gate*, not a permission.
  `POST /invoices/captures/failures/{channel}/{ref_id}/acknowledge` declares
  `require_perm(authz.Permission.INVOICE_WRITE)`; nothing proves a user lacking it is refused.
- **Impact:** the permission declaration is untested, so a future refactor could drop it
  silently. The declaration itself was read and is correct.
- **Recommendation:** one test per new mutating route asserting a role without the permission
  gets 403.
- **Resolution:** **FIXED** — same file. An EMPLOYEE (holds INVOICE_READ, lacks
  INVOICE_WRITE) can READ the worklist but is refused 403 on acknowledge. The assertion is
  403 and not 404 on purpose: the gate must fire BEFORE the handler resolves the reference,
  or a caller without permission could probe which references exist from the error shape.
  Proven non-vacuous by seeding: removing the route's `require_perm` turns it red.
- **Status:** FIXED

---

## F-09 — the frontend has no unit-test infrastructure at all

- **Severity:** P3 · **Confidence:** high · **Category:** test coverage
- **Evidence:** `grep -rn "vitest|jest" frontend/package.json` → no matches. The only
  frontend testing is Playwright e2e, and `test:e2e` runs an explicit allow-list of specs
  that does **not** include any spec covering the new `CaptureFailures` page.
- **Impact:** `frontend/src/pages/CaptureFailures.tsx` (247 lines, new) has zero automated
  coverage. Its behaviour — including the acknowledge flow and the conditional Retry button —
  is verified only by the type checker and the build.
- **Recommendation:** either add a `captures-failures.spec.ts` to the e2e allow-list, or
  accept the gap explicitly. This is a pre-existing repository condition, not something this
  change set introduced.
- **Resolution:** **FIXED for this page** — `frontend/e2e/captures-failures.spec.ts` (6 tests)
  is in the `test:e2e` allow-list. It covers the remediation rendering, the multi-select, and
  most importantly asserts the client posts a CORRECT `agreed_count`: that value is half of
  L-4's guard 1, and a client that computes it wrongly disables the guard while every
  server-side test still passes. Proven by seeding a wrong count (two tests go red).
  The repository still has no frontend UNIT-test runner — that part of F-09 stands.
- **Status:** PARTIALLY FIXED

---

## Deliberately NOT reported

I checked these and found nothing worth reporting; recording them so the absence is
distinguishable from an unexamined area:

- **Import cycle** from `extraction_provider.py` → `capture_failures.py`: none.
  `capture_failures` imports only models, and the module imports cleanly in isolation.
- **`CaptureError` blast radius**: it subclasses `ValueError`, so all 36 existing
  `except ValueError` sites keep their behaviour. The two that matter
  (`email_intake.py:180`, `extraction.py`) now classify rather than merely record.
- **Tenant registration**: both new tables are in `TENANT_MODELS`, both have RLS enabled +
  forced + policy'd in their own migration, and both have real parity probes (not EXEMPT
  rows) added in the same commit.
- **Migration linearity**: `alembic heads | wc -l` → `1`.
- **Secrets / PII in audit meta**: the new audit events carry a captured supplier name, a
  channel key, an operator note and counts. No IBAN, token, password or file content.
