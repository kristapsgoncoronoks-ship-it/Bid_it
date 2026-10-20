# R010 — manual expense draft completion

**Implementation status — 20 October 2026:** the bounded EX09 and EX05-MANUAL
client repair is implemented in the isolated candidate based on main
`e5b101756c83f1668925e44e667bc0ae72f564d4`. The bounded manual slice is locally qualified;
fresh hosted full qualification is pending. The [implementation report](../security/R010-MANUAL-DRAFT-2026-10-20.md)
records executed checks, evidence classes and limits. Existing R010
import/selected/removal work is preserved; parent R010 acceptance and other
manual-editor actions remain separate. The original action assignments below
retain their development and acceptance scope.

Dependency is satisfied: main `e5b10175` includes the expense command foundation
through PR #67 and the completed CI/dependency follow-up through PR #70. [Current source preflight](R010-MANUAL-DRAFT-PREFLIGHT-2026-10-20.md)
inspected PR #70 head `340f7033` and confirms the remaining client gaps. The
implementation uses a clean isolated checkout of refreshed main; preserve the
existing identity-owned query client and identity headers. The preflight is
read-only, not implementation or browser/PG completion evidence.

## Implementation clarifications

The implemented EX09 recovery uses the exact frozen in-memory intention for an
explicit original-save retry; it does not persist the payload or rebuild from
current fields. A typed receipt is checked first, and replay after a 404 keeps
the same key. After refresh or unmount loses the envelope, only outcome lookup
is available. The known-ID readback retry remains GET-only.

Each save intention also captures a lifetime signal. Form unmount aborts pending
client dispatch. A separate status controller prevents an unmounted lookup from
clearing the current form's reference without showing its ID; status helpers
validate lifetime before dispatch and after await. Cancel merely closes the
editor. An abort after dispatch does
not undo server commit and must preserve the original outcome reference. The
implementation report records the reproduced navigation race, its repair,
executed tests and remaining qualification limits separately.

## EX09 — Save draft

Purpose: create one standalone manual draft with exact items/totals and a
recoverable command outcome. It does not submit, approve, reimburse or create a
bank transaction. Existing module, membership and `expense.read`/`expense.write`
authorization remain authoritative.

Baseline source: `frontend/src/pages/Expenses.tsx` NewReport directly posted without
a key, summed with Number, then closed before unawaited refresh. Candidate
`expense_commands.create_report` already supports explicit keys, atomic report /
items / command / audit, and scoped status reads. Reuse that server path.

**Development prompt — R010-A-EX09-P01 r1**

Connect NewReport to the existing command helper with a distinct manual slot
such as `/expenses/manual`, scoped by user and organization. Capture one logical
save key and immutable in-memory payload before asynchronous hashing or sending;
concurrent callbacks share that intention. Send Idempotency-Key and expected
identity headers. Persist key/digest only by default, retaining unresolved
metadata after ambiguous responses, 429, refresh or changed input. Do not persist
expense comments/files/full payload without an approved retention design. A
second independent intention may have identical values and a new key.

Use supported nonnegative two-decimal strings, integer-cent summation and the
existing `decimalMoney` formatter. Invalid/nonfinite/excess precision gets a
field-specific error and zero POSTs; two `0.005` inputs must request correction.
Preserve zero drafts, server q2/ROUND_HALF_UP behavior for legacy clients,
existing currency/FX/tax rules and money limits. Do not install a decimal library
or alter the API contract merely to hide the preview discrepancy.

Preserve Saving/error states. Freeze the submitted envelope or retain newer
unsaved edits separately. A successful command shows its ID and awaits targeted
report/list/summary readback. A failed read after commit shows recorded outcome
plus read failure; it never posts a replacement. Unknown outcome retains the
original reference and offers bounded status checking. If refresh lost original
inputs and no receipt is found, explain the unresolved state and block blind
replacement. Permission/module/identity failures give an appropriate next step.
Closing or cancelling the editor must not imply cancellation of a committed or
in-flight server command; preserve its outcome reference. Manual open/add/remove
and other editor controls retain their separate unreviewed acceptance scope.

Inspect `Expenses.tsx`, `invoiceCommands.ts`, `format.ts`, the owned query client,
expense routes/commands/services/schemas and existing live harness. Add server
code only when an independent failing case proves a missing guarantee. No new
table, job, provider, execution engine or migration is currently justified.

Acceptance: actual browser/API/PostgreSQL `0.10 + 0.20` yields one `0.30` draft,
two items, one command and one correctly targeted audit after a committed-but-lost
response and recovery. Independently read database counts. Verify refused
overprecision, exact large arithmetic, double activation, audit rollback,
changed-input conflicts, identity switching, 401/403/429/5xx/offline, refresh/back
and current-read failure. Keep mocked error rendering distinct from actual
completion. Existing API/auth/upload limits, visual baselines and CI gates stay
active. Measure this manual workflow separately from import benchmarks.

Rollback may disable the new manual UI with a reason; preserve server receipts,
audit, saved references and legacy behavior. Do not replace recovery with a blind
POST or content-only deduplication.

## EX05-MANUAL — Check previous manual draft outcome

Purpose: inspect an existing manual save without issuing another financial
command. This is a **proposed instance of EX05**, not another discovered runtime
action. It uses only the current user's/organization's saved manual key/digest.

**Development prompt — R010-A-EX05-MANUAL-P01 r1**

After EX09, add one conditional, accessible “Check previous manual draft
outcome” button. It performs only scoped GETs against the existing typed
`/expenses/commands/{key}` envelope. Before clearing metadata require
`status=committed`, `operation=report_created`, nonempty `target_id` and
`result.id == target_id`. The current generic helper clears on any successful
GET: introduce the smallest validation/adapter hook and regress existing invoice,
cash and selected callers if that shared seam changes.

Keep manual and selected slots separate. Never call `retrySavedExpenseReport`
with manual data. Disable while checking or without eligible authority/module;
explain missing/corrupt metadata. A 404 means no recorded result found now, not
proof that an in-flight write never commits. Malformed/wrong-operation 200,
401/403/429/5xx/offline/timeout retain unresolved metadata and appropriate safe
next steps. Do not invent or discard a key, poll in the background or post to
discover the outcome. Show the original recorded ID separately from current
approval/deletion/read availability; a failing current read does not undo commit.

Acceptance: after an actual committed response is lost, two lookup activations
make only GET requests, resolve the same report ID and leave independent
PostgreSQL report/item/command/audit counts unchanged. Fixture setup POSTs are
separate from the zero-POST assertion. Test malformed envelopes, missing/corrupt
metadata, unresolved 404, rate limits, changed authority/identity, late responses,
simultaneous manual/selected references and current-read failure. Retain existing
RLS and status-route permissions; no schema or server change is expected.

Rollback hides/disables this instance with an explanation and preserves command
evidence and unresolved references. Keep prompts in maintainer documentation;
product help explains purpose, outcomes and recovery.

AI-assisted: the original source analysis and plan drafting are preserved here.
Implementation, executed tests, actual PG/browser proof and resource measurements
are classified separately in the implementation report. Fresh exact-head CI and
review remain publication requirements; no external code was copied.
