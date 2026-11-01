# Report decisions cannot record reimbursement payments

The report decision API previously accepted `mark_reimbursed` and legacy
`reimburse` on approved or queued reports. It changed the terminal status and
timestamp without the canonical payout receipt, batch maker-checker or keyed
audit/outbox transaction. Synthetic HTTP and native PostgreSQL controls reproduced
an approved report returning 200 and becoming reimbursed through this shortcut.

This candidate starts from fully qualified PR #98 source
`eb02cd2ef0323734ed8f8cac47f8975cdb925d4a`. It is AI-assisted implementation and
independent adversarial review, not human/Lead approval or a production release.

## Change and transaction boundary

The report state service recognizes both old action names but refuses them with
409 `reimbursement_batch_required`, directing the caller to Reimbursements.
Neither name appears as an available report transition. Existing module,
permission, tenant, self-decision and stale-version checks retain their order;
unauthorized or stale requests do not receive a new bypass. Refusal occurs before
status, timestamp, version, audit or webhook changes. The report route's terminal
timestamp writer and terminal webhook branch are removed.

Approval, rejection, return for correction and `mark_for_reimbursement` keep their
existing behavior. The accepted action literals remain in the request schema so
older callers receive actionable conflict guidance. No route or response field is
added. The generated OpenAPI delta is only the existing decision description;
all 417 paths and all component schemas remain.

The existing payout command, lock order, maker-checker/override, optional payment
reference, exact reviewed amounts/versions and shared expense-command identity
are unchanged. It remains the only runtime writer of terminal reimbursement.
This change records no bank submission or confirmation and does not backfill
historical reports. Existing legacy reimbursed facts stay readable.

The report page replaces both terminal buttons with **Open reimbursements** and
explains where payment is recorded. Approved reports retain **Mark for
reimbursement**; own-report/nonapprover views get guidance without new progression
controls. The report payload has no batch ID, so the link uses the existing
reimbursement page rather than inventing a specific batch destination.
At enlarged text on a 390px viewport, an independently reproduced existing
two-column summary overflow is corrected with a one-column small-screen layout
and wrapping. The new link also stays within the card's content width. No global
button styles or visual goldens change.

## Evidence

Detailed sanitized receipts and source-bound checks are recorded in the
[machine receipt](../reviews/evidence/REIMBURSEMENT-TERMINAL-BOUNDARY-2026-11-01.json)
and `/workspace/artifacts/reimbursement-terminal-boundary-2026-11-01/`.
Qualification results are added after execution; pending checks are not passes.
Existing happy-path tests now complete through a distinct canonical payer rather
than deleting their terminal status/timestamp assertions.

| Executed check | Observed result |
| --- | --- |
| New boundary and existing expense API | 76 passed, zero skips; 48 new cases; 56.57s |
| Existing canonical create/pay/SoD/export API | 93 passed, zero skips; 107.14s |
| Native reimbursement concurrency | 38 passed, zero skips; eight new cases; 34.50s |
| API/ERD/pairing/authz/boundary/tenant registration | 54 passed; 5.09s |
| Documentation guards | Seven passed; 0.05s |
| Focused payment browser | 36 passed, one attempt each; included in the full run |
| Full functional browser | 949 unique cases passed, one passed attempt each, zero retries/skips/flakes/runner errors; 345.79s |
| New connected browser/API/PostgreSQL journey | One passed in 66.17s, including the existing auth-window pacing; no route mocks |
| Frontend lint/build/proxy/bundle | All passed; 850 modules, 19 proxy cases, 437.8/129.0kB within unchanged 460/135 budgets |
| Source checks | Ruff check/format, mypy 436 files, PII working-tree scan pass |
| Migration compatibility | Fresh disposable PostgreSQL 16.15 upgrade and Alembic check pass; one unchanged `f0b2d4e6a8c1` head |

The native tests assert non-superuser/NOBYPASSRLS, tenant context and six
FORCE-RLS snapshot tables. Four alias/lock-order schedules observe actual
PostgreSQL blocking and preserve exactly one canonical payment receipt/audit/
webhook/job. The connected journey declares synthetic approval/authority setup,
then uses actual browser queue/create/pay with distinct maker and checker, six
legacy-action denials across approved/queued/paid states and exact committed
history/readback equality. It proves existing maker-checker, not a new
claimant-versus-payer rule, approval-chain journey or bank processing.

The 36 mocked cases overlap the 949; do not add them. The connected case is
separate; the nine existing live cases are byte-preserved and remain in hosted
CI. Backend collection found 5,304 cases, not a full local execution. Two existing
mypy notes, synthetic short-test-key warnings and the empty PII denylist notice
remain recorded. No new local 13-design-page visual run or production-capacity
claim is made. Fresh delivered-head CI remains required; parent PR #98 success
does not qualify these new files.

The original portable RED selection also matched `partially_approved`; those two
cases failed for missing guidance code, not successful payment. The two approved
alias cases and independent native approved case reproduce the actual terminal
bypass. Preserve that distinction. The first native probe ran after shared source
changed and is labelled candidate GREEN; a separate unchanged-parent worktree
provides the native RED. Mocked browser states remain distinct from connected
browser/API/PostgreSQL completion.

The first native candidate run had four new fixture failures because its second
actor lacked the existing approver designation; the fixture now appoints it
through the authorized Team API. The first browser whole-page width failure
was reproduced on old source before its local layout repair. Two new tests also
failed the unchanged lint gate's positive-anchor rule; explicit rendered-status
assertions precede their absence checks. The earlier full run was intentionally
stopped for that correction: 308 passed, four interrupted and 637 unstarted,
exit 130. These attempts are preserved, not relabelled passes or test waivers.

## Backlog and policy boundary

[Decisions §33](../DECISIONS-NEEDED.md#33-manual-reimbursement-evidence-and-exported-instruction-disposition-o05-rb03)
explicitly leaves EX17 unblocked. RB04's active durable-file lifecycle still
needs retention/legal hold, download audience and replacement/disposition rules.
Engineering may prepare its foundation and synthetic proofs; an unused internal
framework is not a completed product fix. This slice addresses the independent
payment-evidence bypass while keeping RB04 open.

The RB04 source review confirms that workspace/archive export row types import
single-use email redemption and unrelated expiry. Reuse storage/job mechanics,
not those row types or retention. A future dedicated artifact must freeze source,
expected bytes/hash/message identity, use the existing command namespace, verify
stored bytes before ready and expose authorized status/exact-byte GET without
new export effects. `jobs.run_once` rolls back handler writes on failure; setting
failed then raising is not a persisted status. Current storage has no global
object-list/sweep primitive, so orphan cleanup requires an explicit design.
Export count also matters: current export markers do not increment batch version.
[Preserved architecture review and prerequisites](../reviews/REIMBURSEMENT-ARTIFACT-FOUNDATION-REVIEW-2026-11-01.md)
records the source findings and proposed native design without approving policy.

Keep RB04 → reviewed RB05/EX22 whole-batch or child-allocation contract → EX21
worksheet, full EX19 recovery, EX16 continuity and the remaining report/finance/UI
queue. Existing optional-reference policy is preserved. B00/F10/readiness and
human/accountant/provider acceptance remain open; feature CI does not close them.

## Action-specific maintenance prompts

| Control | Description and development prompt |
| --- | --- |
| Report decision aliases | Recognize both legacy names and refuse before mutations. Preserve access/self/version checks and stable conflict code; prove committed report, approval, batch, receipt, audit, webhook and job rows remain identical. Never synthesize a batch or payment from a decision request. |
| Mark for reimbursement | Queue an approved report without recording payment. Preserve current version and canonical lock coordination; test subsequent distinct-payer batch completion and stale/direct-payment races. |
| Open reimbursements | Navigate to the existing payout workflow without a write. Keep guidance readable, link keyboard reachable, no direct-terminal request, and no own/nonapprover progression grant. Preserve historical readback and current error/recovery controls. |
| Record payment | Continue using the canonical keyed payout command with exact reviewed children/total, maker-checker, optional reference and atomic receipt/audit/outbox. Preserve original-key replay and distinguish operator recording from unknown bank confirmation. |

## Rollback and review

No model, migration, stored-money rewrite, dependency, workflow, permission or
production configuration change is needed. Application rollback must retain the
direct-terminal refusal and a working payout link; do not restore the unsafe
shortcut as a fallback. Historical data is untouched. Human/Lead review must
explain the invariant and transaction boundary before any merge. No main merge,
provider action, production migration or deployment is authorized by this slice.
