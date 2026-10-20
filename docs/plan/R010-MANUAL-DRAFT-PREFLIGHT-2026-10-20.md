# Manual expense draft preflight — 20 October 2026

**Historical read-only preflight:** the findings and proposed regressions below
retain their original source scope. The [implementation report](../security/R010-MANUAL-DRAFT-2026-10-20.md)
records the later client repair and executed qualification; this preflight does
not itself qualify that implementation.

[Source-bound evidence](../security/evidence/R010-MANUAL-DRAFT-PREFLIGHT-2026-10-20.json)

EX09 and EX05-MANUAL remain actionable. The expense-command foundation is already
in main `d772a366` through PR67; the plan's unmerged/stacked dependency paragraph
is stale. This read-only preflight inspected PR70 head
`340f7033e6e82676ec8c926318c24336c5119612`; no source or GitHub state changed.
The companion JSON binds source hashes, classifications and proposed regressions.

## Confirmed gaps

`NewReport` in `frontend/src/pages/Expenses.tsx:364` still posts directly without
an explicit command key, sums amounts through `Number`, resets/closes on success
before unawaited broad invalidation, and has no manual outcome reference or
lookup control. Editing, add/remove and Cancel remain active during the write.
The manual control has no local `expense.write` gate; server authorization remains.

This is primarily client completion work. The server deliberately gives each
unkeyed manual request a new `legacy-manual` UUID (`expense_commands.py:267`).
Do not replace that compatibility behavior with content-only deduplication:
independent identical drafts are legitimate intentions. Explicit keys already
provide payload-bound replay, live actor locking, report/item construction and
atomic command/audit commit. The total/VAT limit is `999999999999.99`.

The exact JS preview expression gives **€0.01** for two `0.005` inputs; the actual
server `q2` helper gives two `0.01` lines and **0.02** total. This was reproduced
as an isolated arithmetic diagnostic only. It is not new browser/API/PG evidence.
Reject excess precision in the manual form before POST; preserve legacy server
ROUND_HALF_UP behavior and valid zero drafts.

Two existing protections must be preserved: `api.ts:29` already supplies expected
identity headers, and `main.tsx:60` gives the entire app an identity-owned query
client that remounts on identity/permission changes. They are not missing.
Capture the manual intention's explicit scope before asynchronous work rather
than depending on whatever identity exists later.

## Endpoint and helper seam

| Endpoint | Meaning |
| --- | --- |
| `POST /expenses` | HTTP201 report detail; accepts explicit Idempotency-Key |
| `GET /expenses/commands/{key}` | Typed committed receipt: id/status/key/operation/target_id/result |
| `GET /expenses/commands/{key}/result` | Raw saved result snapshot, used by current selected/import/remove controls |
| `GET /expenses/{id}` | Current report state, separate from original committed receipt |

Both lookup routes retain expense.write, module, employee and organization
scoping. Do not weaken them to make a recovery button available.

`checkSavedInvoiceCommand` removes metadata after any 200. Both successful
lookup paths in `invoiceCommand` do the same. A manual caller cannot validate
an envelope after that deletion. The smallest shared change is an optional
lookup-result validator/adapter, defaulting to current behavior for existing
callers and invoked before metadata removal in every success branch. The manual
adapter should require committed/report_created, matching saved command key,
nonempty target, and an object result whose id equals target_id; narrow any other
fields needed for display. Malformed/wrong-operation 200 must retain metadata.
If the seam changes, regress invoice, cash and selected callers as well.

`invoiceCommand` awaits digest generation before it creates a key or reads the
slot. NewReport must synchronously capture a stable key, explicit scope and
immutable payload in the Save event handler, then pass that captured intention
as mutation variables. Coalesce concurrent callbacks in memory. A React pending
state alone does not close the asynchronous window. Persist only key/digest in
a distinct `/expenses/manual` slot; never pass manual data through the selected
report retry helper or persist comments/full expense payload by default.

Keep a known committed ID visible through readback failures. Await targeted
report/list/summary readback before complete success; a read failure must not
cause another POST. Unknown outcomes retain their original key. After a refresh
has lost original inputs, a 404 means no receipt visible now, and cannot authorize
blind replacement or a new key. Closing the editor does not cancel server work.

## Strongest regressions, in order

1. Actual manual browser/API/PostgreSQL `0.10 + 0.20`, inject loss only after the
   actual response has committed. Independently assert one report, two items,
   one command and one correctly targeted audit; recover the same ID and 0.30.
   Two later lookup activations perform only GETs and leave counts unchanged.
2. Browser zero-POST precision/invalid/negative/nonfinite/range refusals for both
   amount and VAT, including two `0.005` inputs. Exact cent preview and supported
   boundary arithmetic; retain valid zero drafts and server rounding contracts.
3. Hold digest completion, activate Save twice, then edit or close. Verify a
   single frozen intention/key/scope and no replacement write; test changed input
   against unresolved metadata and a later independent identical intention.
4. Typed lookup malformed/mismatched/wrong-operation 200, 404, 401/403, 429,
   5xx/offline. Assert metadata retention where outcome remains unknown, zero
   POSTs for lookup, and clear permission/connection recovery. Mocked failures
   prove rendering/recovery only; keep real completion separate.
5. Refresh/back, missing/corrupt metadata, simultaneous manual/selected slots,
   identity switching and late responses. Preserve the existing global query
   client and response generation filter. No cross-scope data or blind new key.
6. Commit followed by current-report/list/summary read failure: show committed ID
   separately from current availability; never post a replacement.
7. Narrow manual API/PG regressions for explicit-key replay, independent identical
   keys, changed payload, same-key race and audit rollback. Reuse existing actor,
   tenant/RLS and atomic transaction guarantees; add server code only if these
   independently fail.

## Existing harness and prerequisites

`frontend/live/expenses.spec.ts` has six real PG/browser contracts covering
import/selected/removal completion and recovery, but none exercises manual Save.
Its actual response-loss injection and independent psql counts are directly
reusable. The hosted PostgreSQL job already starts `r010_browser_server` and runs
`npm run test:expenses-live`. One worker and the 61-second owner pacing preserve
the production auth limiter; do not disable it. Keep fixture POSTs outside
zero-POST lookup assertions. The existing resource middleware can measure this
manual journey separately from import capacity claims.

No owner decision, new library, table, migration, provider, worker or execution
engine is needed for the bounded slice. Start from refreshed main in a clean
isolated checkout; do not import the original dirty UI/auth/AP workspace wholesale.
The native browser/PG journey, manual resource measurements, focused cross-caller
regressions and fresh hosted qualification remain implementation acceptance work.
No proposed fix was implemented or qualified by this preflight.
