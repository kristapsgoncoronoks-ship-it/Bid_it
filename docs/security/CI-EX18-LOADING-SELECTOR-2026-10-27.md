# CI-EX18-01 — reimbursement loading selector

27 October 2026. PR #86's original source `f43017c0ee9b519fc1843d7f723b4a979a3f24da`
completed [CI 40499764181](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/40499764181)
and its supply-chain workflow successfully, but the functional browser job had
**871 first-attempt passes and one flaky retry success**. It was not a
retry-free hosted run. Earlier local 872-case qualification remains valid for
its original invocation; it does not erase this hosted failure.

## Cause and bounded fix

The loading/error case in `frontend/e2e/reimbursement-create.spec.ts` used
`getByText(/Loading.*(?:eligible|approved|report)/i)`. While the organization list
was loading, Playwright matched both the eligible table's status cell and the
sidebar ancestor containing its own loading text and report navigation labels.
The strict locator failed; a later retry passed after the sidebar finished.
No duplicate financial request or application-state defect follows from this
test failure.

The existing case now holds organization and eligible-list reads concurrently,
then locates each exact, anchored `role=status` message independently. It still
asserts no false empty queue, visible eligible-list failure after a 503, and zero
financial POSTs. Releasing organization loading must leave the eligible error
visible. This strengthens the original contract; no new case or implementation
behavior is introduced. There is no `.first()`/`.nth()` escape, added delay,
timeout increase, retry change, skipped assertion, gate change or golden update.

The frozen spec SHA256 is
`cf67fa3147e3f88e1507421d0af4080de301254e7e1a9a44af0c7a55eda1fe1f`.
All application runtime files remain byte-identical to `f43017c`.

## Evidence and action prompt

- Focused creation suite: **18 passed**, zero retries/skips/flakes, 26.16 seconds.
- The strengthened concurrent-loading case: **10 repetitions passed**, zero
  retries/skips/flakes, 11.17 seconds. These repeat one case, not ten new journeys.
- A copied spec with only the old selector restored produces **one expected
  strict-locator failure**, 2.01 seconds, matching the hosted two-element failure.
  The product and committed candidate files were not edited for that control.
- Full default functional browser suite: **872 passed**, zero retries/skips/
  flakes, 563.22 seconds. Focused/repeated cases overlap this coverage.
- Frontend lint/type/structural checks pass. Independent source/evidence review
  found no unresolved blocker. Fresh hosted status is recorded separately in the
  [repair receipt](evidence/CI-EX18-LOADING-SELECTOR-2026-10-27.json).

**Prompt `CI-EX18-01-P01 r1`:** preserve the exact eligible and organization
status roles/copy when maintaining the loading/error contract. Hold both reads
through their separate loading and error transitions; assert no financial POST
or globally empty queue. Drive the concurrent state deterministically, without
waiting for unrelated navigation to settle. Restore the broad selector only in
an isolated negative control and prove it fails. Keep retries, timeouts,
assertions, financial behavior and baselines unchanged; record each invocation's
first-attempt/flaky counts rather than equating workflow success with clean
execution.

Local artifacts are under
`/workspace/artifacts/reimbursement-ci-selector-2026-10-27/`. Original hosted
evidence is preserved under
`/workspace/artifacts/reimbursement-create-2026-10-27/ci/`: final log excerpts,
the exact failure, tested-tree identity, archive digests/CRC, ten image identities
and twenty exported report hashes. Those images have zero reported HIGH/CRITICAL
rows; their SBOMs retain medium/unscored rows, so they are not vulnerability-free.

The original hosted backend passed 4,573 cases (200 skipped), PostgreSQL 218,
ordinary visuals 13, and existing connected expense checks nine. Those nine are
the existing CSV/manual fixtures; the new reimbursement journey is qualified by
its separate local unmocked evidence. Counts from separate invocations overlap
and are not summed.

## Review and remaining work

AI assistance produced the test fix, deterministic control, independent review
and this record. Human/Lead acceptance remains separate. Reverting this test-only
change restores a timing-dependent assertion but changes no application or data
behavior; preserve the original failure before any future test rewrite.

The next ordered product action remains O05-RB03 / EX20: recoverable recording of
an operator's payment assertion, preserving maker-checker and optional-reference
policy under decision 33. Full EX19 recovery, export contracts and external
release controls remain open. No merge, deployment, payment, provider, SMTP,
worker execution or production-data change is performed by this repair.
