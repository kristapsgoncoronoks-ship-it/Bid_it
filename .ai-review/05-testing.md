# Test report

## Tests executed

Commands are the repository's real gates, taken from `.github/workflows/ci.yml`, not invented.

| Command | Result | Notes |
|---|---|---|
| `cd backend && .venv/bin/ruff check app tests` | **PASS** | `All checks passed!` |
| `cd backend && .venv/bin/ruff format --check app tests` | **PASS** | `579 files already formatted` |
| `cd backend && .venv/bin/mypy app` | **PASS (after F-01 fix)** | Was `Found 1 error in 1 file`; now `Success: no issues found in 333 source files` |
| `cd backend && .venv/bin/alembic heads` | **PASS** | Exactly 1 head (`d3b8c05f7a41`) — CI asserts `-eq 1` |
| `cd backend && .venv/bin/python -m pytest -q` | see below | Full suite, final tree |
| `cd frontend && npx tsc --noEmit` | **PASS** | exit 0 |
| `cd frontend && npm run build` | **PASS** | `✓ built in 5.21s` |
| `cd frontend && npx playwright test e2e/nav.spec.ts` | **PASS (after F-02 fix)** | Was `1 failed, 8 passed`; now `9 passed` |
| `cd frontend && npm run test:e2e` | **PASS** | `270 passed (2.5m)` — the full CI e2e allow-list |

Targeted suites run during the build of this change set (before the review):

| Command | Result |
|---|---|
| `pytest tests/test_capture_failure_worklist.py -q` | `15 passed` |
| `pytest tests/test_inbound_channel_health.py -q` | `14 passed` |
| `pytest tests/test_vendor_resolution_provenance.py -q` | `12 passed` |
| `pytest tests/test_tenancy_parity.py -q` | `73 passed` |
| `pytest -k "vendor or invoice or audit or capture or review" -q` | `428 passed, 1 skipped` |
| `pytest -k "invoice or capture or extraction or upload or email or audit or intake" -q` | `494 passed, 1 skipped` |

**Gates NOT executed here, and why:**

- **The GitHub Actions CI run itself** — runners are billing-blocked. Everything above is
  local. This is the single largest verification gap in the review.
- **The Postgres job** (`pytest tests/test_rls.py tests/test_numbering_concurrency.py
  tests/test_transport_lock_concurrency.py` against a real Postgres service) — no Postgres
  available in this environment. **The RLS policies added by the two new migrations have
  therefore never been executed.** They are syntactically identical to the established
  pattern, but that is an argument from similarity, not evidence.

## Coverage of changed functions

Assessed by reading the tests, not by a coverage tool — no percentage is claimed.

| Symbol | Coverage | Test |
|---|---|---|
| `capture_failures.code_for` | direct | `test_classification_is_driven_by_the_exception_type...` |
| `capture_failures.kind_for` | direct | `..._claims_no_cause`, `test_every_failure_kind_carries_advice...` |
| `capture_failures.worklist` (upload channel) | direct | 8 tests |
| `capture_failures.worklist` (**email channel**) | **UNTESTED** | — (F-07) |
| `capture_failures.acknowledge` (upload) | direct | 4 tests |
| `capture_failures.acknowledge` (**email**) | **UNTESTED** | — (F-07) |
| `capture_failures._superseded` | direct | `test_a_capture_that_fails_again_returns_to_the_worklist` |
| `extraction.clear_fields_for_retry` | direct | `test_upload_retry_review_guard.py` (5 tests) |
| `inbound_health.begin_attempt` | direct | `test_an_attempt_that_never_reports_an_outcome...` |
| `inbound_health.record_success` | direct | 3 tests |
| `inbound_health.record_failure` | direct | module-off + malformed-delivery tests |
| `inbound_health.status_for` (all 4 states) | direct | `never_used`, `ok`, `failing`, `silent` each pinned |
| `inbound_health.set_expected_cadence` | direct | set + withdraw + audit tests |
| `vendor_resolution.resolve` (all 3 outcomes) | direct | exact / none / abstain each pinned |
| `vendor_resolution._near_reason` | direct | 3 positive + 3 negative cases |
| `vendor_resolution.audit_meta` | direct | via the audit-record tests |
| New route authorization (permission denial) | **UNTESTED** | — (F-08) |
| `CaptureFailures.tsx` (247 lines) | **UNTESTED** | no frontend unit-test runner exists (F-09) |

## Seeded-violation proofs

The strongest evidence in this change set. Each structural guarantee was proven by
deliberately breaking it, running the suite, watching the expected test go red, and restoring
the source. **11 total, all caught:**

- P0 (3): guard removed in three configurations — 3 of 5 tests went red.
- H-1 (4): an old ack silencing a new failure; classification removed; `document_retained`
  hard-coded true; the "is this actually a failed capture" guard removed.
- H-2 (4): attempt recorded last instead of pessimistically first; a refused attachment
  counted as a broken channel; a guessed 7-day cadence default; a timeout-less `httpx` client.
- H-3 (3): first-match instead of abstain; resolving after the create; a loose prefix rule
  that collapses distinct suppliers.

## Vacuous assertions

I re-read the three new test files looking for assertions that would pass with the feature
removed or inverted. **None found that survives the seeded-violation evidence** — every
guarantee-bearing test is demonstrated to fail when its guarantee is broken, which is
precisely the property a vacuous test lacks.

Two tests are guard rails rather than proofs, and are labelled as such in the source: "an
untouched capture still retries without ceremony" and "an explicit discard still succeeds".

## Known gaps

1. The **email channel** of the failed-capture worklist (F-07) — the half of the feature
   where silent failure hurts most.
2. **Route authorization** on the new mutating endpoints (F-08).
3. **Concurrency** on `inbound_health.consecutive_failures` — no test either way.
4. **The new RLS policies have never executed** (no Postgres here).
5. **The new frontend page has no automated coverage** (F-09).

## Assessment

Depth is good where it exists: the seeded-violation discipline is stronger evidence than a
passing suite, and it covers every structural claim the three features make. Breadth is
uneven: one whole channel, the authorization layer and the entire frontend surface are
unverified, and two CI jobs (the real runner, and Postgres) have never run against this code.

Test quality is **adequate, not strong** — held down by breadth and by the unrun gates.
