# Manual expense draft completion — 20 October 2026

**Status:** the bounded EX09 and EX05-MANUAL repair is implemented in an isolated
checkout based on main `e5b101756c83f1668925e44e667bc0ae72f564d4`. The bounded
manual slice is locally qualified; fresh hosted full qualification remains pending. This
report does not claim merge, live deployment, complete R010 acceptance or the
completion of the broader backlog.

[Machine-readable evidence](evidence/R010-MANUAL-DRAFT-2026-10-20.json) separates
baseline reproduction, synthetic client faults, server contracts and the real
browser/API/PostgreSQL journey. The [action assignments](../plan/R010-MANUAL-DRAFT-2026-10-20.md)
and [historical preflight](../plan/R010-MANUAL-DRAFT-PREFLIGHT-2026-10-20.md)
retain the original scope and development prompts.

## Problem and resulting behavior

The original form submitted manual reports without an explicit command key,
summed preview amounts through JavaScript `Number`, and closed before its
unawaited readback. An actual browser/API/PostgreSQL baseline confirmed that
two `0.005` entries displayed **€0.01** but committed **€0.02**, with one report,
two items, one command and one correctly targeted audit event. The submitted
request had no explicit key. This is stronger evidence than the earlier isolated
arithmetic diagnostic, but remains an isolated synthetic-data reproduction.

The repaired manual editor accepts supported nonnegative decimal strings with
at most two fractional digits. Amount and VAT arithmetic uses integer cents;
`decimalMoney` displays the exact result. Per-line values and aggregate totals
cannot exceed `999999999999.99`; VAT cannot exceed its expense amount. Invalid,
negative, nonfinite, overprecise or out-of-range inputs request correction
without a POST. Valid zero drafts remain supported. The server's existing
legacy per-line `ROUND_HALF_UP` behavior and monetary API contract are unchanged.

Save captures a new key, copied/frozen payload and explicit user/company scope
synchronously before hashing. A synchronous busy guard coalesces repeated
activations, while controls preserve the submitted envelope. The separate
`/expenses/manual` session slot retains **only key/digest**, never comments,
full payloads, receipts or files. Independent later intentions may have identical
values and a different key. Existing global identity-owned query clients and
expected-identity headers remain in place.

The shared command helper has an optional response adapter. It validates an
opt-in caller's write result or typed lookup before removing metadata. A manual
lookup requires `status=committed`, `operation=report_created`, the saved key,
a nonempty target and matching `result.id`; the narrow report fields used for
display must also be valid. Malformed successful responses retain uncertainty
rather than becoming success. Existing callers without an adapter retain their
raw response contract. A session generation/token guard prevents commands held
at an asynchronous boundary from starting under a changed identity, and prevents
an old canceled response from initiating recovery under a new session.

A separate navigation-lifetime diagnostic held one form's digest, navigated
away and back, completed a new save, then released the old digest. Synthetic
browser requests exposed two independent keys/titles: the obsolete form could
still dispatch under the same identity. A controller is now captured in each
save intention, and NewReport unmount aborts its pending work. The shared helper
accepts an optional `AbortSignal`, checks it at asynchronous boundaries and
passes it to Axios. Closing the editor with Cancel does not abort server work.
An abort after dispatch does not prove rollback: the original key/digest stays
available for a later authoritative receipt lookup. The diagnostic establishes
client stale dispatch, not a second actual PostgreSQL commit.

A related lookup-lifetime regression held a typed status GET across a real SPA
unmount/back transition. Releasing its old valid response cleared the pending
manual reference without showing the saved ID in the current form. A separate
status controller now aborts lookup work on unmount. The shared status helper
accepts an optional signal and checks identity/lifetime before dispatch and
before clearing metadata after the awaited response; direct checks of an
already-known receipt use the same captured lookup signal. Cancel still only
closes the editor. The anchored failure proves metadata loss in synthetic client
state, not a server rollback or a new database mutation.

A known saved ID and its original recorded total remain visible while the
current report, own-report list and summary load. These targeted reads are
awaited. A failed current read shows the saved reference plus a GET-only retry;
it does not create a replacement. The immutable receipt records the original
save, not the report's later approval, deletion or payment state.

## Action contracts

| Control | Description and result | Recovery / maintainer contract |
| --- | --- | --- |
| Save draft | Create one standalone draft using exact amounts, one key and the captured scope. It does not submit, approve, reimburse or create a bank transaction. | EX09: show the recorded ID, await targeted readback and retain unknown metadata. Preserve existing server authorization and rounding. |
| Check previous manual draft outcome | Perform scoped GET requests against the typed receipt; validate it before clearing unresolved metadata. | EX05-MANUAL: 404 is unresolved, not proof of noncommit. Malformed 200, refusal, rate limit or connection failure cannot authorize a new key or POST. Repeated checks retain the original known ID. |
| Retry original manual save | Available only while this mounted form still has the matching original frozen intention. Check the receipt first, then replay the original key/payload only if absent. | EX09 recovery: never rebuild from changed fields, invent a replacement key or enable after refresh/back has lost the original inputs. Same-key serialization preserves one committed outcome. |
| Retry loading saved report | Reload the current report and visible aggregates for a known committed save. | EX09 readback: only GETs; preserve the recorded ID when current availability fails. |

Product guidance explains outcomes and recovery. Engineering prompts stay in
maintainer documentation. Closing the editor does not cancel server work.
Missing/corrupt metadata and missing authority remain explicit; no background
polling or silent reference replacement is added.

## Maintainer prompts for recovery controls

**EX09 recovery — Retry original manual save.**

> Preserve the original EX09 save contract. Enable this control only while the
> mounted form retains its frozen payload, scope, key and lifetime signal and
> the pending reference matches them. Check the typed receipt first; replay the
> exact original key/payload only after a 404. Coalesce repeated activation and
> refuse stale identity or aborted form work. Never reconstruct current inputs,
> create a replacement key or enable replay after refresh/unmount loses the
> original envelope. Prove one committed report/command/audit across response
> loss and replay, with lookup preceding every retry and no extra POST when a
> committed receipt already exists.

**EX09 readback — Retry loading saved report.**

> Preserve the original EX09 committed ID and recorded result. Perform only
> scoped current-report, own-list and summary reads, await their results and
> show an explicit current-availability error when any fails. The receipt is
> the original save, not current approval/payment/deletion state. Keep the ID
> visible through failures; never post a replacement, clear unresolved evidence
> or claim rollback. Verify a committed save followed by failed readback and
> successful GET-only retry, including unchanged independent PostgreSQL counts.

## Qualification and evidence boundaries

The final production/test source snapshot is bound in the JSON. Final focused
runs pass **44 shared-helper cases** (46.41s) and **43 manual UI cases** (43.73s),
with zero skips or flakes. The UI run has no retries and two workers; its source
hashes are unchanged through the run. These use synthetic responses and qualify
client state, request identity and lifetime, not server completion. They include
the held-digest and held-status-GET SPA unmount/back regressions.

A fresh production build and frontend lint pass after the final status guard.
First load remains **434.8 kB raw / 128.4 kB gzip**, within 460/135. The backend
collects **4,591 cases**; collection is not execution. The actual backend CI lint
scope, mypy for 423 application files and seven documentation checks pass.
Independent final-delta review found no unresolved blocker; its held-status
control observes one GET, zero POSTs, no adapter acknowledgement and byte-exact
metadata retention. The unchanged amount helper also passed 124 independent
Python Decimal reference cases and six malformed-response controls.

A fresh, source-frozen actual browser/API/PostgreSQL run passes **all three
manual cases** in 126.83s, with one worker, no retries/skips/flakes and the
production auth limiter/61-second owner pacing retained. Its first case loses
the actual response only after a **€0.30** draft commits, then performs two
user-activated GET-only recovery checks. Independent global and target-filtered
counts remain **one report / two items / one command / one creation audit**;
fixture replay returns the same ID. Fifteen invalid-field cases plus one total
overflow perform zero POSTs, while a valid zero draft commits. The final case
keeps its known commit through one blocked current read, then a real restored
HTTP200 GET; retry performs zero writes and counts remain 1/1/1/1.
The final-source existing invoice/cash browser cohort also passes **12 cases**
in 14.00s with no skips or flakes.

Earlier populations retain their separate scope:

- **Baseline client helper:** 18 failed / 3 passed before the response adapter;
  the three existing passthrough controls passed. The original manual UI had
  two targeted regression failures, with captured synthetic request envelopes.
- **Existing server foundation:** 25 API tests passed, including ten new manual
  cases plus the existing R010 suite. Eight native PostgreSQL cases passed,
  including four new manual cases. Tests cover explicit replay, independent
  identical keyed/unkeyed intentions, payload conflict, unchanged legacy rounding,
  exact boundary/overflow, immutable receipt readback and atomic audit rollback.
- **Native concurrency:** migrated PostgreSQL 16.15, application role with neither
  superuser nor RLS bypass, forced RLS on reports/items/commands. Race tests
  independently observe competing actor-lock waiters before releasing the lock.
  The local database container has 2 CPU / 1 GiB and a 512 MiB tmpfs; the API
  process and browser are outside those database caps. These are not whole-app
  or minimum-hardware measurements.
- **CI integration guards:** 87 focused checks passed after registering the new
  PostgreSQL file in the executable CI list. Both new mocked browser specs are
  explicitly included in the normal frontend command; no coverage exclusion,
  worker/retry increase, rate-limit relaxation or visual approval is added.

The earlier broad local browser invocation passed 687 cases and reported three
worker test-ID mismatches after the spec changed during execution. The earlier
full real-expense run passed eight of nine cases; the invalid-input editor reset
during HMR and timed out. Both mixed-source invocations are retained as
intermediate evidence, never final-source full-suite passes. Fresh hosted
qualification of the normal complete suites remains pending.

The local expenses visual comparison failed with 15,440 differing pixels under
the local Chromium/baseline environment. This test renders the unchanged
`/design` showcase fixture, not the manual editor. No PNG was approved or
regenerated, and no tolerance was raised. The result remains a local failure;
all 13 pinned hosted visual checks remain pending.

Mocked browser errors qualify client rendering and recovery only. Real
completion requires the actual API response to commit before its response is
lost, scoped SQL counts and the same report ID through repeated GET-only
recovery. The manual resource observations are separate from import capacity;
process CPU/RSS totals include auth pacing and all measured journeys. No
per-journey CPU/RSS, final-image runtime, OCR, provider, fleet or production
capacity conclusion follows from these runs.

The fresh manual API process measured **154,388 KiB peak RSS**, 5.094 user /
0.231 system CPU seconds across 193.29 wall seconds. Wall time includes startup,
authentication and owner pacing; RSS/CPU exclude the database, browser and Vite
processes. Four observed report POSTs (including fixture replay) used 54 queries
total and had a 60.19 ms sample p95; two receipt GETs used 14 queries total and a
28.96 ms sample p95. These tiny sample p95 values are observed maxima, not
service-level/capacity results. First-observed list/summary reads included
565–601 ms samples, not controlled cold-start benchmarks. Client elapsed
observations include independent SQL observers and fixture replay. Concurrent
root tests shared the host; no isolated customer-latency claim is made.

The owned API process stopped after resource emission. The owning agent removed
only the labelled temporary PostgreSQL fixture, verified its absence and removed
its private credential state; the cleanup receipt supersedes earlier pending
snapshots. Vite is retained for root checks. No production data, provider, live
host or unowned resource was accessed.

Local Python 3.12.14 and Node 24.19.0 differ from hosted/shipped runtimes. The
local frontend compiler uses retained previously qualified bytes, validated by
the current installer before execution (source/toolchain/license identity and
SHA-256 `09f762da200042fa4ccd3d28dd6e0109c38333840e52e5a3a80c2b4395e3864a`).
This is not a fresh local rebuild; hosted CI still rebuilds normally. Existing
short development JWT-key warnings remain visible; no warning or security gate
is suppressed.

Initial failed test-fixture/command attempts are retained in the evidence:
the API test used a wrong audit attribute before correction; browser fixtures
needed precise selectors and a real-server-equivalent identity refusal; the
initial live overflow assertion used the wrong message. An accidentally widened
Ruff command included CLI scripts outside the actual CI lint scope and reported
45 existing print-rule findings; those scripts were not changed or suppressed.
The actual CI lint scope and mypy for 423 application files pass. Those do not establish
new product defects. The stale-session dispatch diagnostic uses synthetic API
responses; the server's real expected-identity fence would refuse those stale
requests, so no cross-company mutation is claimed.

## Scope, rollback and remaining work

No backend product source, schema/migration, dependency, worker, provider or
new command engine is added. Existing tenant/module/employee permissions,
actor locking, atomic command/audit transactions and append-only evidence are
preserved. The test-only resource middleware accepts a bounded journey label;
it does not change product requests or production observability.

Rollback the manual UI/helper integration together with its regressions while
preserving server receipts, audits and unresolved key/digest evidence. Hiding or
disabling this manual control must explain how to inspect an existing outcome;
it must not clear metadata or replace recovery with a blind POST. Reverting only
the optional adapter removes validation for its callers and must be reviewed as
a connected change.

Other manual open/add/remove/cancel, receipt upload, submission/approval and
R010 parent contracts retain their own acceptance scope. CI feedback timing,
shipped-runtime parity, remaining automation/workflow repairs, historical PII
classification and owner-dependent provider/retention decisions remain open.
The preceding main's CI/release/deployment success qualifies `e5b10175`, not
this repair. Its public-health URL is unset; authoritative live entrypoint,
served revision and public readiness remain unverified.
