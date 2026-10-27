# Reimbursement reliability — ordered action prompts, 27 October 2026

These are maintainer assignments, not product instructions or executed work.
[Source/evidence reconciliation](../reviews/DAILY-REPORT-RECONCILIATION-2026-10-27.md).
Preserve the attachment's O05-RB01/02/03 and EX18/19/20 IDs; refinements below
address independently verified source details and keep unresolved policy explicit.

## Shared prerequisites

Repository: `kristapsgoncoronoks-ship-it/SME-financial-workspace`. Inspected main:
`90891d8d52cf5aefc4467e3fa7f6070c042acec2`; reviewed candidate:
`011be654f3fd580eaa5962b077fe319b9bd42099`. Refresh current refs, applicable
AGENTS.md, README, engineering/AI rules, tenancy/audit/idempotency/outbox ADRs,
O05/R010/EX10 and CI before editing. Use an isolated reviewable branch; preserve
accepted work, organization/module/EXPENSE_APPROVE boundaries, maker-checker,
Decimal/string currency, audit history and existing gates.

For standalone use, copy these shared prerequisites with the selected action
prompt. Name the command grain explicitly. Inspect and preserve the current
expense receipt's tenant/actor/key uniqueness and operation/payload mismatch
refusal rather than silently introducing a second cross-action key policy.
Include immutable canonical inputs/version. Authorize status lookup to that
identity; a timeout does not establish rollback, and client pending state does
not replace the database concurrency guarantee.

Entry points: `backend/app/services/reimbursement.py`,
`backend/app/api/routes/reimbursements.py`, `backend/app/schemas/reimbursement.py`,
`backend/app/models/expense.py`, `frontend/src/pages/Reimbursements.tsx`,
`backend/app/services/expense_commands.py`, expense routes/state, audit/webhooks,
and existing reimbursement/export/SoD/PG concurrency tests. Reuse installed
React/Query, SQLAlchemy/PostgreSQL, current finance-command patterns and persisted
jobs. No new framework/provider, live transfer, bank upload, message, migration
against production, merge or deployment is authorized by these prompts.

## First — O05-RB02 / EX19 Cancel

**Purpose:** an unexported open batch may be cancelled; a committed export must
not silently release its reports for another instruction.

**Prompt:** refresh the shared prerequisites. Reproduce the export-first then
cancel sequence using synthetic reports/artifacts. In the existing locked batch
transaction, refuse cancellation after committed export evidence, paid state or
unsafe legacy disposition, before unlink/status/version/audit mutations. Keep
existing authorization; introduce no new role rule. Return a stable typed conflict
and authorized recovery facts. Correct the confirmation and provide an accessible
blocked explanation with a bank-disposition/reconciliation next step. Use an
existing supported route or honest operator guidance, without a placeholder
button. Block conflicting operations for the same pending batch; do not imply reversal or
settlement. The minimum export guard may be reviewed first, explicitly leaving
version/reason/frozen key, atomic receipt/audit/event policy, status lookup and
original-key replay as tracked completion work. Reuse the EX10-compatible command
pattern for that completion; do not auto-retry an uncertain cancellation.

Add a read-only preflight for exported-open/cancelled-exported batches, broken
links and contradictory payment state. Never relink, pay, reverse or invent
historical evidence automatically. No schema is required for the minimum
predicate; any later receipt/reason migration must be additive, registered for
RLS/model parity and have empty/representative upgrade, refusal and rollback
evidence. Preserve approved reports and export-once behavior.

**Acceptance:** actual API/PG export followed by cancel returns a typed no-write
refusal with links/status/count unchanged and zero cancellation audit/event;
ordinary unexported cancellation still works. Run real separate-connection
export/cancel and pay/cancel schedules in both orders through the route transaction
seam. Cover tenant/role denial, stale version, repeated key, changed payload,
response loss, 429/5xx/offline and identity/navigation changes. For the full receipt
slice, recover the original result before re-batching. Keep mocks limited to UI
failure presentation. Run relevant current gates and record commands, attempts,
fixture counts, lock waits, queries, CPU/RSS and cold/warm latency. Report which
parts remain open; a minimum guard is not complete EX19 acceptance.

## Next — O05-RB01 / EX18 Create payout batch

**Purpose:** the exact observed eligible selection creates one recoverable batch
with exact totals and report links, or creates none.

**Prompt:** refresh shared prerequisites and coordinate the EX19 guard. Freeze
tenant/actor, unique ordered report IDs/versions, supported method/note and a
canonical command key/digest. Reject duplicate/oversized selections. Lock reports
in deterministic order and recheck active tenant membership, permission,
eligibility, version, unbatched state and EUR evidence after locking. Commit one
batch, exact Decimal total, unique links/counts and durable receipt with the
approved audit/outbox policy atomically; verify best-effort webhook defaults
rather than assuming they guarantee intent. Same-key replay returns the same
result; changed payload conflicts. Add authorized key lookup for a lost create
response. Bound/paginate eligible and batch reads, include both approved and
marked-for-reimbursement states, and retain existing links. Expose total/empty/
loading/error states. Replace UI Number/raw-currency fallback with existing exact
formatting/decimal helpers and explicit incomplete conversion. Clear selection
only after committed receipt/readback; fence late results on identity changes.

Preflight soft/dangling/cross-tenant report links and empty/orphan batches before
any composite FK/index migration. Refuse unresolved anomalies; preserve historical
records and provide additive migration/RLS/parity/rollback evidence. Do not infer
actual corruption from missing constraints alone.

**Acceptance:** normal browser/API/PG/readback creates the exact unique set and
count; foreign conversion gaps refuse. Exercise two actors with the same/overlapping
selection, opposite input order, stale edits, changed tenant/role, duplicate IDs,
0/1/1000 reports, audit/event failure and lost responses. Repeated controlled PG
races must not leave a duplicate/orphan committed batch, duplicate intent or
contradictory links. Verify all pages beyond 100 rows without full-list materialization.
Record exact counts/artifacts, focused/all applicable gates and bounded
CPU/RSS/query/bytes/lock/latency measurements; do not equate mocked 200s with
persistence or measure a new capacity claim without a controlled baseline.

## Then — O05-RB03 / EX20 Record payment

**Purpose:** record an authorized operator's payment assertion and its evidence
without representing it as bank/provider confirmation.

**Prompt:** refresh shared prerequisites and the shared receipt design. Preserve
batch lock/version checks, exact totals, current maker-checker and explicit audited
platform-admin override. Retain existing method/reference/time and audit actor
evidence. Add an explicit manual/legacy/provider evidence basis and authorized
readback; keep confirmation unknown without actual provider proof. Review UI copy
and per-batch pending/conflicting controls. Freeze payload/key/version, commit
terminal state, approved audit/event intent and receipt atomically, and resolve
unknown outcomes by key before another command. Required payment reference,
method/date/attestation fields must follow the owner decision in
`DECISIONS-NEEDED.md` section 33; current null-reference behavior is intentional,
so do not impose a new policy silently. Legacy unknown evidence remains unknown.
Do not fabricate provider confirmation, payer/date/reference or rewrite hashed
audit history. Plan additive schema/event compatibility, consumer preflight,
RLS/parity checks and evidence-preserving rollback.

**Acceptance:** maker refusal and authorized checker success remain intact;
normal API/PG/browser/readback records only the approved evidence fields once.
Test policy validation, same/different key replay, postcommit response loss,
pay/pay and pay/cancel route-level races, identity changes and failure/recovery
states. Check legacy null-reference rows remain reportable with explicit unknown
basis. No terminal duplicate audit/event/report transition, false provider claim
or silent partial result is acceptable. Run current relevant gates; measure
resource/latency/query effects using synthetic data. Provider acceptance and
bank/jurisdiction qualification remain separate future work.

## Remaining lane

Give EX21 CSV and EX22 SEPA their own artifact/download/re-export/skipped-payee/
recovery/interlock contracts before claiming the payout workflow complete. Resume
EX17 direct-terminal closure and EX16 queue continuity, then EX12–EX15/EX11 on
the shared report cycle/receipt design. Existing RN03/RN06/RN07–RN09 and ACC01
retain their prompts and accountant/resource acceptance. Do not copy a generic
workflow engine or increase commercial scores merely because this plan grew.
