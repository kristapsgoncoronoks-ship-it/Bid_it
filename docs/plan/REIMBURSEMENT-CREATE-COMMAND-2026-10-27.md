# EX18 — exact, recoverable reimbursement batch creation

27 October 2026. O05-RB01 / EX18 is implemented as a review candidate on the
EX19 minimum-guard parent `262d4b4791d6bb5992b5870781eebc181c5599a9` (PR #85).
This is creation and bounded-read work, not completion of reimbursement payment,
cancellation recovery, bank exports or the wider backlog. The
[ordered action contracts](REIMBURSEMENT-RELIABILITY-2026-10-27.md) remain the
parent plan. No production data, historical links, schema or provider is changed.

**Hosted update:** the original implementation head `f43017c` completed all core
and supply workflows, including 4,573 backend / 218 PG / 13 visual passes, but
one functional browser case required a retry (871 first-attempt passes plus one
flaky success). The local results below describe their original invocations.
[CI-EX18-01](../security/CI-EX18-LOADING-SELECTOR-2026-10-27.md) records the exact
failure and subsequent spec-only repair. Application runtime remains unchanged;
new test-source qualification is separate from the original hosted result.

## Result and transaction boundary

A keyed create freezes the actor/organization, unique sorted report IDs and
observed versions, method and note. The existing `expense_commands` unique
`(org_id, employee_id, command_key)` identity is reused, including cross-operation
and changed-payload conflicts. No second key namespace, table, job runner or
workflow engine is introduced.

Creation locks and refreshes the active membership, rechecks EXPENSE_APPROVE and
the expense module, then locks reports in ID order using PostgreSQL FOR UPDATE
and `populate_existing`. It checks the exact selection, current versions,
approved/marked-for-reimbursement state, absence of a batch and EUR evidence
before allocating a batch. Duplicate/invalid IDs, empty selections and more than
1,000 reports are refused. A supplied key requires every observed version.
Method is bounded to the existing 40-character column; note has a 1,000-character
request bound. No new payment-method enumeration or evidence policy is invented.

The transaction stores one batch, Decimal total, report links and one version
increment per report, the immutable receipt, the existing reimbursement audit,
and strict keyed webhook delivery/job intent together. An audit, receipt or
outbox failure rolls back these writes. Strict behavior is scoped to this
financial command, following existing expense/issued-command precedents; global
best-effort audit/webhook defaults are unchanged. No subscriber means no delivery
is needed; a queued intent is not external delivery or payment confirmation.

The server returns the original frozen result for same-key/same-input replay,
even if the batch is later cancelled or paid. Current state is read separately.
`GET /reimbursements/commands/{key}` is actor/tenant scoped and rechecks current
approver access. The existing `/expenses/commands/{key}` and `/result` aliases
also enforce EXPENSE_APPROVE for the new reimbursement operation, preventing a
demoted creator from retrieving other employees' reimbursement details through
the older WRITE-only route. Other expense operations retain their permissions.

Legacy POSTs without keys retain repeat-refusal and legitimate re-batching
semantics using a fresh legacy receipt identity. They do **not** gain lost-response
recovery. A deterministic fallback from report IDs alone was rejected because it
could return an old cancelled batch when a legacy client intended a new one.

Membership authority is linearized by its lock. Module enablement is checked
after that lock using the existing expense-command convention; this change does
not establish a new immediate module-disable policy for commands already waiting
on report locks. It does not lock every session, user or module row.

## Reads and interface

`/reimbursements/eligible` returns paged unbatched approved **and** marked reports
with observed versions and reliable nullable EUR figures. `/reimbursements/page`
returns paged batches and grouped report counts. Each page is at most 100 rows;
eligible reads use two domain queries and nonempty batch pages use three, without
materializing every report once per batch. Ordering includes an ID tie-breaker.

The legacy array GET supports explicit pages and `X-Total-Count`. If an implicit
legacy read would omit rows, it refuses with typed 422
`reimbursement_pagination_required` and directs the caller to explicit pagination
or `/page`; it never silently claims the first 100 are the whole population.
Small implicit responses keep their old array shape. Consumers with more than
100 batches must adopt the explicit page contract.

The UI retains selections and observed versions across pages. Exact minor-unit
arithmetic and `decimalMoney` replace floating sums and the raw foreign-total
fallback. Missing/invalid EUR conversion is explicit and not selectable. The
server still owns eligibility, conversion and Numeric range validation.

Before sending, the UI freezes one key/digest/payload in the existing
actor/organization/action session-storage grain. No token is stored there. A
synchronous guard prevents double activation before hashing yields. The saved
reference remains until the committed receipt, current authorized detail and
refreshed visible lists are verified. A disappearing last page is clamped and
its replacement fetched before the reference/selection clears. Known created
batch actions remain blocked while that verification is unresolved.

Transport failures, native 15-second timeouts, malformed receipts, 5xx and
key/payload conflicts retain the reference. **Check saved batch** uses only GET.
A missing receipt is not proof of rollback; only an explicit **Retry original
request**, which checks again first, may resend the same key and frozen payload.
Definite validation/selection refusals permit correction while retaining the
selection. Identity changes and navigation abort/fence continuations and preserve
the original account's reference. Session storage supports reload/navigation in
that tab; closed-tab/browser recovery without the retained key is not qualified.

Loading, failure, empty-page and total states are distinct. Header/actions wrap;
the native eligible table has column relationships and a named keyboard-focusable
local scroll region. All money, currencies and actions remain available through
native layout and local scrolling. Existing
EX19 exported-cancellation help, export acknowledgements and maker-checker behavior
remain intact. Shared-shell and whole-app accessibility are not certified here.

## Action descriptions and maintenance prompts

These prompts are maintenance assignments, not instructions to the running app.
Use the shared prerequisites in the parent plan with each prompt.

| Action / prompt | Description and regression assignment |
| --- | --- |
| Create payout batch — `EX18-CREATE-P01 r1` | Freeze exact selected IDs/versions, scope and key before awaiting. Verify membership/report locks, unique links, Decimal total and atomic receipt/audit/intent. Exercise normal, stale, overlapping/opposite-order, maximum selection, rollback and repeated-key paths through real API/PostgreSQL; retain frozen evidence on uncertain responses. |
| Check / Refresh saved batch — `EX18-LOOKUP-P01 r1` | Use the original actor/tenant/key in authorized GET only. Validate operation, key, target, selected IDs and exact total, then verify current state and lists. A 404, malformed reply, timeout or denied read must retain the reference and must not post or enable a replacement request. |
| Retry original request — `EX18-RETRY-P01 r1` | Offer only after an explicit missing-outcome check; check again before sending. Reuse the original key and immutable payload. Prove response-loss/reload/navigation/account changes cannot introduce a new intention or duplicate financial/audit/outbox rows. |
| Refresh eligible reports — `EX18-REFRESH-P01 r1` | Perform a bounded read, preserve selected snapshots, expose changed versions for explicit review and distinguish failure from an empty queue. Do not silently replace selected versions or reinterpret missing foreign FX as EUR. |
| Previous / Next — `EX18-PAGE-P01 r1` | Page eligible reports and batches independently with total/loading/error states. Preserve selections across pages; verify every page beyond 100 rows. When totals shrink, fetch a valid page before claiming successful readback. Keep batch report counts at constant query count. |
| Clear selection — `EX18-CLEAR-P01 r1` | Clear only local selection when no unresolved create exists. It must issue no financial request and must never discard a saved uncertain command. Verify keyboard and pending-state behavior. |
| Open selected report / Expenses — `EX18-NAV-P01 r1` | Preserve the authorized report link and parent navigation. Leaving a pending create must fence late continuations, retain its original reference and permit GET-only recovery on return; replacement accounts cannot continue it. |

## Qualification and limits

- **79 API tests passed**, including 31 new creation cases: exact/immutable replay,
  changed inputs/operations, actor/tenant/role/module boundaries, 0/1/1,000/1,001
  report boundaries, missing FX/overflow, strict rollback, paged eligibility and
  legacy pagination refusal. Existing manual/R010 and reimbursement controls pass.
- **18 PostgreSQL tests passed** on the migrated local PostgreSQL 16.15
  non-superuser/NOBYPASSRLS role: eight retained controls plus ten new cases.
  Eight repeated actual HTTP schedules cover same/opposite-order/partial-overlap
  selections and same-actor/same-key replay. Distinct PIDs and observed database
  blocking prove production locks; winner snapshots equal the state after loser
  and retry. Two additional schedules prove post-wait membership-role/version
  refusal. Removing only the report lock in an isolated child interpreter makes
  the regression fail; candidate files are unchanged by that control.
- **54 focused browser checks passed** without retries/skips/flakes: 18 new
  creation cases, 14 retained cancellation cases and 22 existing read-failure
  cases. The old frontend fails the foreign-missing-conversion regression.
  Native 15-second request aborts, loss/lookup/replay, reload/navigation/account
  fencing, malformed/failed readback, exact money, page shrink and known-batch
  interlocks are covered. Mocked responses qualify UI behavior, not persistence.
- Final default functional browser suite: **872 passed**, zero retries/skips/
  flakes, 525.04 seconds. Focused cases are included rather than additional
  unique coverage. No local ordinary-visual pass is claimed for this head.
- An **unmocked browser → Vite proxy → API → PostgreSQL** journey passes with
  102 synthetic reports across two pages. The UI creates the selected approved
  and marked reports for exactly EUR 0.30, leaves 100 reports unchanged, and stores
  one receipt/audit/queued delivery/job. Authorized lookup, same-key replay and
  changed-payload refusal preserve complete compared rows. Approval states alone
  were ORM-seeded. No actual network-loss fault, transfer, export, email or worker
  delivery was injected into this connected journey.
- API schema generation updates only the four intended reimbursement paths and
  four request/page schemas, with zero removals; its truth checks pass after an
  expected pre-generation drift failure. README service count reflects the new
  service. PostgreSQL CI-registration coverage passes all 40 cases.
- Final TypeScript/lint, whole-app mypy (429 files), Ruff, build and unchanged
  bundle gates pass: 846 modules; **437.6 kB raw / 129.0 kB gzip** against 460/135.
  Impeccable's source detector completes with no primary findings, but `.tsx`
  receives regex-only analysis; contrast/parsed-element rules did not run.
- Rendered desktop and narrow captures were inspected. At 390px and 32px root
  text, document/body widths are 390px and the table scrolls locally; native
  ArrowRight advances its scroll position. This qualifies the observed O05-UI01
  body containment, not all devices, screen readers, 320px content combinations,
  shared-shell design or WCAG conformance. No golden/tolerance was regenerated.

The full functional browser result, source identities and hosted status are
recorded in the [machine receipt](../security/evidence/REIMBURSEMENT-CREATE-COMMAND-2026-10-27.json).
Local evidence is under `/workspace/artifacts/reimbursement-create-2026-10-27/`.
The preceding PR #85's completed hosted checks qualify its own source, not this
new candidate. Fresh complete hosted CI and human/Lead review remain required.

The API cohort used 149.91s wall / 92.76s user / 9.34s system and 352,704 KiB
peak pytest RSS including setup; the PG cohort used 19.26s / 16.21s / 0.46s and
186,876 KiB. The connected Node helper used 11.46s / 0.78s / 0.12s and 150,284
KiB, excluding browser/API/PG processes. Read-count fixtures prove at most three
domain SELECTs for 1 versus 20 batch rows. These are bounded shared-host process
observations, not controlled cold/warm throughput or production hardware sizing.

Harness failures and superseded runs are retained/classified: invite acceptance
expected 200 instead of actual 201; unavailable `/usr/bin/time`; two interim UI
mock/navigation timing mistakes; setup attempted before local API readiness.
They are not counted as application failures or additional coverage.

## Review, rollback and remaining work

AI assistance produced implementation/tests/docs; separate source/adversarial
review and connected checks informed the candidate. The adversarial objections
about receipt access after demotion, legacy silent truncation, last-page shrink
and known-batch actions during verification were resolved before final checks.
The simpler client-only pending guard was rejected because it cannot serialize
different approvers or preserve a lost committed result. Human/Lead acceptance
and integration decisions are still pending; no self-approval, merge or deployment.

No migration is needed because existing receipt/RLS/uniqueness infrastructure is
reused. The report-to-batch soft reference remains; no composite FK, historical
repair, migration preflight/rollback or global writer guarantee is claimed. Use
the existing read-only preflight before any later constraint/data repair. Never
relink historical reports or reset export/payment evidence automatically.

Code rollback requires coordination with clients using the new pages/lookup;
the old server cannot provide their recovery contract. Preserve all receipts,
audit/outbox rows and operator references, stop new creation during a rollback,
and resolve uncertain commands before falling back. Reverting the locks reopens
the overlap defect. There is no schema/data downgrade or automatic compensation.

Full EX19 reason/version/receipt recovery, EX20 payment-evidence policy and
command recovery, EX21/22 artifact contracts, EX17 direct-terminal closure and
the rest of the ordered finance/UI queue remain open. Decision 33's optional
reference/maker-checker policy is unchanged. Release controls B00/F10/readiness,
accountant acceptance and production resource qualification remain separate.
