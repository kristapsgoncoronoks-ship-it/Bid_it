# Implementation log — changes made during this review

This review was scoped as review-first. Four changes were made, all of them repairs to
defects the review itself surfaced. No feature work, no refactoring, no cosmetic edits.

## 1. `backend/app/services/invoice_pdf.py` — fix the red `mypy` gate (F-01)

**What:** declared `seller_keys: tuple[str, ...]` before the credit/payment branch.

**Why:** `mypy app` is a CI gate and it was failing. mypy inferred `tuple[str, str]` from the
credit branch and rejected the 3-tuple in the payment branch.

**Note on attribution:** this was introduced by `b6d12db`, earlier on this branch, **not** by
the change set under review. I fixed it anyway because leaving a knowingly broken CI gate in
place is worse than the small scope expansion, and because it is one annotation with no
runtime effect.

**Alternative rejected:** making both branches the same arity (padding the credit tuple)
would have changed what the credit note prints. The type was wrong, not the data.

## 2. `frontend/src/lib/nav.ts` — rename the new nav item (F-02)

**What:** `"Unread documents"` → `"Failed captures"`.

**Why:** the label broke `e2e/nav.spec.ts:125`, which asserts the admin-gated `"Documents"`
link is hidden from a base user. That assertion matches by accessible name **without**
`exact: true`, so `"Unread documents"` substring-matched it.

**Alternative rejected — and this is the important one:** adding `exact: true` to the test.
That would have made a correct assertion weaker to accommodate a weak label. The test caught
a genuine UI problem: two sidebar items where one label contains the other read as
parent/child, and "unread" wrongly suggests an inbox state rather than "the machine could not
read this". `"Failed captures"` also pairs correctly with the "Captures" item above it.

## 3. `frontend/src/pages/CaptureReview.tsx` — stop resolving on every keystroke (F-04)

**What:** keyed the `/vendors/resolve` query off `useDeferredValue(draft?.vendor_name)`
instead of the live value.

**Why:** the vendor input calls `setDraft` per keystroke, so a 20-character supplier name
fired ~20 requests, each scanning up to 2000 vendor rows server-side.

**Alternative rejected:** a hand-rolled `useDebounce` hook. No debounce utility exists in this
codebase, and `useDeferredValue` is built into React with no new dependency and no timer to
leak. Resolving on blur was also considered but is worse: the panel would stay stale while
the user looks at it.

## 4. `backend/app/api/routes/email.py` — make the two inbound routes symmetric (F-03)

**What:** moved the module gate and both health writes inside `set_current_org(org_id)` in
the Mailgun handler, matching the generic handler.

**Why:** they ran outside the tenant context, disabling the ORM tenant guard (layer 2 of
three) for those statements. Not a leak — both statements filter `org_id` explicitly — but
the belt was off, and two sibling routes doing the same thing differently is how the wrong
pattern gets copied.

## Cleanup

Removed `backend/tests/test_zz_probe.py` — an untracked scratch file left behind by a review
subagent that was killed mid-run. Its own docstring read *"TEMPORARY probe test - reviewer
scratch. Deleted before finishing."* It was breaking `ruff check` with unused imports.

## Commands executed

```
# static gates (repository's own, from .github/workflows/ci.yml)
cd backend && .venv/bin/ruff check app tests                  -> All checks passed!
cd backend && .venv/bin/ruff format --check app tests         -> 579 files already formatted
cd backend && .venv/bin/mypy app                              -> 1 error -> (fix) -> Success, 333 files
cd backend && .venv/bin/alembic heads                         -> 1 head (d3b8c05f7a41)

# frontend gates
cd frontend && npx tsc --noEmit                               -> exit 0
cd frontend && npm run build                                  -> built OK
cd frontend && npx playwright test e2e/nav.spec.ts            -> 1 failed -> (fix) -> 9 passed
cd frontend && npm run test:e2e                               -> 270 passed (2.5m)

# backend suite
cd backend && .venv/bin/python -m pytest -q                   -> see 05-testing.md
```

## Remaining work (not done here, deliberately)

- F-05 paginate the failed-capture worklist (design change, not a review edit)
- F-06 an explicit `failed_at` column instead of reusing `updated_at`
- F-07 test the email channel of the worklist
- F-08 authorization tests on the new mutating routes
- F-09 decide whether the new frontend page gets e2e coverage
- Move `CaptureError` to `app/core/` to fix the import direction
- Regenerate the deploy runbook for the two new migrations
