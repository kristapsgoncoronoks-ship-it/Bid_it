# Reimbursement export reliability — assignments from the 29 October report

These are proposed maintainer assignments. They do not execute the attachment,
approve a financial policy, merge a branch or enable a provider. Preserve the
original prompt IDs and revisions; do not add a second executor or command-key
namespace. [Source/evidence reconciliation](../reviews/DAILY-REPORT-RECONCILIATION-2026-10-29.md).

## Shared source and review boundary

Refresh repository `kristapsgoncoronoks-ship-it/SME-financial-workspace`, current
main and all dependency PRs. Inspected main is `90891d8d52cf5aefc4467e3fa7f6070c042acec2`;
qualified development parent is PR #95 at
`740c722caa6add90844c6cc7ee462a9ad47a15b5`. PR #96 is a separate older-main-based
adapter candidate; review/reconcile it rather than duplicating its implementation
or copying older locks, workflows, generated contracts or frontend layout.

Read applicable AGENTS.md, README, engineering/AI rules, tenancy/audit/outbox and
idempotency ADRs. Inspect current reimbursement routes/service/commands/models,
`expense_commands.py`, storage/jobs, `workspace_export.py`, `archive_export.py`,
export download/redemption/purge and existing SEPA guards. Frontend entry points
are `pages/Reimbursements.tsx`, `components/AccountingExport.tsx` and `lib/api.ts`.
Keep existing permissions, maker-checker/override evidence, Decimal/currency
rules, report/batch lock order, command identity and immutable history. No live
bank submission, payment, message, dependency upgrade or production migration.

## O05-RB04-P01 r1 — immutable export artifact and recovery

**Purpose:** an export whose response is lost is recovered as the same file,
not a newly numbered instruction. Current counters/audits do not preserve bytes.

**Prompt:** use an isolated branch and the shared review boundary. Reproduce the
current commit-before-download gap with synthetic data. Reuse the smallest native
receipt/job/storage lifecycle; document whether a dedicated artifact model is
needed. Freeze tenant/actor, batch/report IDs and versions, exact totals/currencies,
format/profile, command key/payload digest, filename/content type, row count,
message ID where applicable and predecessor/reason. Persist hash, byte size,
ready/failed/unknown and retention/purge evidence. Validate original source facts
under canonical locks; never recompute a different file under an existing key.

Separate keyed creation from authorized status and exact-byte GET. Same key and
payload recover the original receipt; changed payload conflicts. Commit receipt,
export marker, audit and keyed job/outbox intent consistently. If object storage
is involved, ready requires verified stored bytes/hash; use existing persisted
jobs and orphan cleanup instead of claiming database/object atomicity. Re-download
does not increment export count or create a message ID. Server delivery is not
proof of browser disk persistence or bank acceptance. Regeneration requires its
own identity and approved predecessor/disposition contract.

Any migration is additive, tenant/RLS registered and single-head, with explicit
impact/preflight/refusal/postcondition/rollback evidence. Inventory legacy counters
without artifacts read-only; preserve them as unknown, never invent bytes/hash.
Keep Decisions §33 retention, replacement and external-disposition decisions
explicit. Do not pick a storage provider or expiry merely to complete a schema.

**Acceptance:** real PostgreSQL same/different-key races, stale versions,
membership/permission loss, audit/job/storage failure, missing/corrupt object,
purge, double-click and response loss. A real browser/API/PG journey arms the
download event, saves and parses the file, compares its hash/size/source to the
committed artifact, refreshes and downloads identical bytes twice. Status-only
recovery makes no new artifact/audit/payment. Keep mocked UI failures distinct.
Measure existing versus candidate CPU/RSS/query/latency/bytes/queue completion at
1/100/1000 rows where admitted; preserve existing limits rather than widen them
for a benchmark. Run relevant native/migration/tenant/frontend/security gates and
report exact source, attempts, cleanup, rollback and unresolved decisions.

## O05-EX22-P01 r1 — recoverable, truthful SEPA instructions (O05-RB05)

**Purpose:** an omitted employee cannot become reimbursed through a later
whole-batch payment record. An instruction is not submission or settlement.

**Prompt:** refresh the shared source boundary and RB04. Independently reproduce
the acknowledged partial-file versus whole-batch payment sequence on synthetic
data. Review Decisions §33: the report proposes first-release all-ready/all-included
generation, with partial allocation deferred. Do not silently change the existing
supported acknowledgement policy or invent bank/profile requirements. If partial
support is approved, define explicit per-report export/payment allocation and
remaining-unpaid state before using it; omission must never imply paid.

Under the accepted contract, revalidate current batch/report versions, exact EUR
total, active membership/permission, payer/payee readiness and selected profile
inside the canonical command/lock boundary. Distinguish a new instruction from
GET of a retained artifact. New paid/cancelled/stale/mixed-readiness instruction
eligibility must follow the reviewed rule and fail without marker/audit/artifact
mutation. Explain exact blocked rows and a real repair/rebuild/disposition next
step. Preserve maker-checker and optional manual-reference policy.

Build from frozen source using the existing SEPA renderer; preserve exact
MsgId/EndToEndId/hash/size, profile/validation provenance and complete transfer
count/sum. Lost response recovers the same artifact and message ID. Do not
auto-regenerate, auto-submit, mark bank accepted or settle omitted reports.
Any future bank-specific profile needs a selected pilot bank/entity and reviewed
contract; EPC/XSD material alone is not acceptance.

**Acceptance:** real PG/browser saved XML for a two-report fixture. Mixed readiness
produces the accepted blocked/allocated outcome and no false payment; after repair,
transfer identities/count/sum equal the reviewed child set. Verify exact-byte
re-download, stale/two-session/re-export/payment/cancel races, role/tenant loss,
timeouts and current-state readback. Parse actual saved XML, inspect committed
rows/receipt/audit/jobs and measure resources separately from provider acceptance.
Keep source-bound CI and policy review explicit. RB05 has this EX22 prompt; there
is no separate RB05-P01 block in the uploaded register.

## R010-A-EX17-P01 r1 — close direct terminal reimbursement

**Purpose:** report-level progression cannot bypass canonical payout evidence.

**Prompt:** refresh the shared source and the original EX17 assignment retained
in the register. Trace both direct `mark_reimbursed`/legacy aliases and batch
payment through expense decision, state machine, locks, permissions, audit and
webhooks. Reproduce direct approved/queued-to-reimbursed progression without a
batch receipt. Close the bypass by reusing the approved payout command/evidence
boundary; preserve approve/reject/return and legitimate queue progression. Do not
invent a new role rule, required manual reference, bank confirmation or historical
backfill. Keep already reimbursed legacy evidence as unknown where appropriate.
Prove actual API/PG denial/no-write and authorized canonical completion, claimant
and maker-checker constraints, stale/report-payment races, idempotency and UI
readback/recovery. Report unresolved policy and rollback, not a guessed paid state.

## O05-EX21-P01 r1 — labelled payout worksheet CSV

**Purpose:** a human/accountant gets a provenance-bearing worksheet, with no
unsupported bank/payroll import or download-persistence claim.

**Prompt:** refresh the shared source and RB04. Inspect all CSV consumers before
versioning columns or copy. Label the action and artifact as a payout worksheet
unless a named importer contract is separately approved. Generate from the frozen
artifact source: batch/report IDs and versions/state, employee ID/display name,
report title, exact original currency/amount, reporting EUR and FX provenance,
method, artifact/profile identity and generation time. Preserve formula-injection
protection, Decimal strings and explicit unavailable FX; do not add unnecessary
bank details. Review open/paid/cancelled eligibility and retention rather than
guess a policy. Re-download uses the same artifact, never a new export command.

**Acceptance:** actual browser/API/PG CSV save and parse, exact rows/order/values,
hash equality across refresh and repeated GET, approved status labelling, large
decimals/formula payloads, wrong tenant/permission, stale source, response loss,
429/offline/server failure and identity changes. Prove zero duplicate artifact/
audit/job for original-key recovery. Measure resource bounds and the unchanged
bundle budget; separate mocked failure copy from saved-file completion evidence.

## Candidate handoff: ACC02/ACC03 r2 in PR #96

Review the already implemented guard/schema/capability/accessible blocked-action
candidate rather than assigning it again. Compose only reviewed feature changes
onto current qualified development source. Retain current responsive layout and
contrast, combine browser scenarios, regenerate additive OpenAPI from the combined
app, and preserve current dependency/workflow/image security repairs. Qualify
scoped backend/native/browser/identity/permission and complete required CI. Keep
Xero/QuickBooks disabled; human schema/accessibility review and provider/template
selection remain separate. No provider adapter is accepted by a passing blocked-
action test, and no PR instruction in the attachment authorizes a main merge.
