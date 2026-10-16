# R016 — Replay-safe legal credit commands (pilot-discovered)

AI-assisted implementation/test/evidence drafting; human review and exact-head CI
pending. Added while exercising the October 16 reports' controlled finance pilot.

Two independent regressions reproduced the defect: retrying one partial-credit
command created two legal notes; retrying a full-credit command returned a failure
because the first response had already consumed the remaining balance. A parent
row lock alone prevents lost updates/over-crediting; it cannot identify a retry.

Credit issuance now requires Idempotency-Key, binds parent/kind/typed payload, and
stores the key/hash in existing issued-document command columns. No new table or
migration. Under the existing parent/issuer locks, a known command returns its
original note before reevaluating the changed balance (200 replay, 201 creation).
Changed inputs or a different financial action conflict, and uniqueness races roll
back number allocation, credit accumulation, audit and webhook intent together.
A parent-scoped status endpoint resolves an uncertain committed response. One
command produces one legal number and one audit/webhook intent.

The full-credit browser control binds the reviewed credited_total; a concurrent
change returns credit_balance_changed before mutation. It retains account/workspace
command identity, offers Check previous credit, keeps pending through readback, and
resolves a lost response before another write. Existing confirmation and role checks
remain; partial credits are still an API operation. Older API writers must provide
keys, and should supply expected_credited_total when binding a reviewed balance.
Missing valid-command identity returns 428; source and legal numbering history are
not backfilled or rewritten.

Evidence: both original regressions failed, then passed. **89 related backend tests**
passed without changing their monetary expectations. Three keyed-command tests plus
real PostgreSQL concurrency passed (four tests): distinct concurrent €300/€400 intents
still accumulate €700, while two callers replaying one additional €100 command create
one note and finish at €800 credited / €200 outstanding. Changed inputs, missing key,
stale credited balance, scoped lookup and single audit are covered. Whole-app mypy
(418 files), frontend lint/build/bundle and issuing checks passed. Seven affected
browser tests passed, including a lost-response credit that makes one POST, resolves
the saved command and reads back Fully credited. These error branches are mocked;
the pilot separately exercises the real API/browser/worker/database path.

Action / prompt: Credit issues one legal correction of the reviewed remaining amount;
Check previous credit reads its committed result after uncertainty. Preserve locks,
tenant/permission scope, one-number/audit/webhook identity, and readback recovery.
Test partial/full replay, balance conflicts, cross-action keys and PostgreSQL races.
Never deduplicate by amount/content alone, and never issue a second note to discover
whether the first request committed. Roll out paired API/UI; rollback reintroduces
this release blocker and must not be treated as a safe replay policy.
