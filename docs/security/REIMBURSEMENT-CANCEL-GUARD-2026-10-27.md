# EX19 minimum export-cancellation guard — 27 October 2026

An open reimbursement batch with a committed CSV/SEPA export could previously be
cancelled, releasing its reports for another batch while the original bank file
might still be in use. This candidate refuses that cancellation before report
reads/unlinks, state/version changes or cancellation audit writes. The existing
batch row lock already serializes export/pay/cancel; the missing state predicate
was the defect.

This is the **minimum O05-RB02 / EX19 repair**, based on documentation PR #84 at
`904527b2bcc3294b6e9351205b7858a4afd69a25`. Full cancellation command receipts,
reason/version requirements and keyed unknown-outcome recovery remain open under
the [parent action contract](../plan/REIMBURSEMENT-RELIABILITY-2026-10-27.md).
It is not completion of the whole reimbursement workflow or wider backlog.

## Result and action contract

The service returns **409 `batch_exported_cannot_cancel`** when any export record
exists: nonzero `export_count`, non-null `exported_at`, or non-null `last_msg_id`.
This conservatively includes negative counters and partial/empty non-null legacy
message markers. The normal non-open-state refusal is unchanged. Authorized
unexported open batches still cancel with 204, release their reports and write
the existing single cancellation audit.

The UI keeps Cancel visible but natively disabled with an associated persistent
explanation. It directs the operator to check file disposition with their bank
or accountant and distinguishes export from payment confirmation. Confirmation
for the normal case accurately describes returning reports to the payable pool
and acknowledges that the server can refuse a newly recorded export/payment.
No placeholder reversal or bank-integration action is introduced.

Cancel, pay, export and payment-reference editing are excluded for the same busy
batch until a fresh authorized detail GET and approved-list refresh complete.
The current detail read cancels stale list work and updates that batch's cached
row; it does not reuse a pre-command list response. Failed reads retain the
original command error and keep competing controls blocked, with an explicit
**Refresh batch status** GET-only recovery. The two newly awaited reads have
15-second request bounds. Existing re-export/skipped-payee confirmations wait
until refresh completes, preserving their explicit acknowledgements and flags.

This reads current batch state; it is not a durable command-key status lookup.
Existing financial write transport/unknown-response semantics and full response-
loss/navigation recovery remain separate work. No cancellation reason, request
version or new receipt schema is added in this minimum slice.

| User action | Complete result in this slice | Safe recovery / remaining boundary |
| --- | --- | --- |
| Cancel a recorded export | Disabled/explained in the page; direct API receives typed 409; batch/report/audit/outbox state stays unchanged | Verify instruction disposition; no automatic relink, reversal or replacement file |
| Cancel an unexported open batch | One 204, one ordinary cancellation audit, report released, refreshed current state | Existing state refusal protects a repeated ordinary call; keyed replay is not implemented |
| Refresh batch status | Authorized bounded GET plus current approved-list read; controls unlock only after successful readback | Failed/timeout read stays blocked with an explicit GET-only retry; no write is repeated |
| Export/re-export or acknowledge skipped payees | Existing file/acknowledgement behavior retained with same-batch exclusion and awaited readback | Artifact generation is not bank submission or settlement; EX21/22 remain independent contracts |

Tenant/module/EXPENSE_APPROVE checks, batch maker-checker, Decimal totals,
currency/FX behavior, successful API schemas and export-once rules are preserved.
Payment references remain optional under existing policy. There is no application
dependency, migration, permission, external provider, SMTP or worker change.

## Preflight and historical limit

[Read-only tenant preflight/runbook](../plan/REIMBURSEMENT-CANCEL-PREFLIGHT-2026-10-27.md)
reports bounded examples and complete category counts for recorded exports,
cancelled links, empty batches, payment mismatches and unresolved soft links.
It never repairs records. An unresolved example may include a selected report's
stored foreign batch ID, but no foreign batch row is enumerated.

Migration `d4e6f8a0b2c4` backfilled old export markers to zero/null. Those values
cannot establish that an older instruction was never generated. The new guard
protects recorded markers; it does not certify historical absence or recover
lost report associations. Preserve unknown evidence and obtain a case-specific
operator disposition under decision 33. No production preflight or data repair
was run here.

## Qualification actually executed

- Original API control: two export-first cases fail on the old 204; ordinary
  cancel/tenant/role controls pass. Separate in-process original-function PG
  controls fail both export-first cases without editing product files.
- Relevant API/SEPA/maker-checker/export cohort: **33 passed**. Includes complete
  batch/report and audit/delivery/job snapshots, non-vacuous queued webhook
  evidence, all legacy-marker variants and ordinary cancellation/readback.
- Migrated PostgreSQL 16.15, non-superuser/NOBYPASSRLS: **8 passed**, including six
  actual HTTP route schedules for CSV/cancel, SEPA/cancel and pay/cancel in both
  orders. Distinct backend PIDs and observed database blocking prove the lock;
  losing requests/retries preserve the winner's committed snapshot.
- Focused browser: **14 passed**, covering accessible blocked states, normal
  confirmation, stale 409/readback, pending interlocks, failed-read GET retry,
  actual held-request 15-second timeout, existing export warning acknowledgements
  and narrow/enlarged-text payout-card bounds. The immutable control fails the
  recorded-export disabled-button case.
- Final default functional browser suite: **854 passed with zero retries**.
  The focused fourteen are included, not additional unique coverage.
- Actual unmocked browser → Vite proxy → API → PostgreSQL acceptance passes:
  two UI-created batches, completed/hashed CSV download, refused exported DELETE
  with identical financial/audit/outbox rows, and normal UI cancellation with
  one audit and released report. Approval state alone was explicitly ORM-seeded.
- Native preflight: two synthetic organizations, ten batches, twelve reports;
  nine asserted review categories, read-only transaction verified, missing/invalid
  org and injected UPDATE refused, selected/control financial/audit hashes unchanged.
- Type/lint/Ruff and bundle gates pass; 845 modules, **437.5 kB raw / 129.0 kB
  gzip** within unchanged 460/135 budgets. Final Impeccable detector returns `[]`.
  PostgreSQL CI-registration contract: 40 passed; existing registered file extended.

The same-fixture desktop/enlarged captures were inspected before/after. The
changed payout card fits at 390px/32px text; inherited header/approved-list page
overflow remains (497px document, formerly 927px). This is not full-page reflow,
physical-device, screen-reader or complete accessibility certification. No visual
golden or tolerance changes. Fresh hosted full CI remains required for the new
candidate; the completed #84 results qualify its parent only.

**Open O05-UI01 / prompt O05-UI01-P01 r1:** re-inspect the reimbursement page's
header and approved-report table with ordinary/long synthetic content at320/390px
and 200% text. Reproduce the inherited document overflow against the immutable
baseline, then wrap/contain those sections while preserving every amount,
currency, status, selection and action. Use native keyboard access and meaningful
table relationships; do not clip facts to fit. Verify actual text/control/page
bounds, loading/error/retry states and unchanged write payloads with before/after
captures. Keep this separate from command receipts, payout policy and financial
arithmetic. Current payout-card fit is not acceptance of that full-page task.

## Evidence, resource scope and review

[Machine receipt](evidence/REIMBURSEMENT-CANCEL-GUARD-2026-10-27.json) binds final
source, tests and artifacts under
`/workspace/artifacts/reimbursement-cancel-2026-10-27/`. Failure-first controls,
fixture/collection errors and superseded source checks remain separately classified.
The first preflight missing-argument exit-status issue was caught on native psql
16, corrected before publication and rerun against the same fixtures.

Connected observations include a 213-byte synthetic CSV, request IDs, local
export/refusal/cancellation response-time headers and helper CPU/RSS. They are
descriptive shared-host observations, not controlled capacity or improvement
benchmarks. Browser/API/PostgreSQL memory and production headroom are not inferred
from Node-helper RSS. Full cold/warm/1–1000-report resource acceptance remains open.

AI assistance produced implementation/tests/docs, independent source/adversarial
review and connected acceptance. The alternative of leaving cancellation enabled
after file generation relies on unknowable external disposition; retaining links
is the safer bounded behavior. Existing permissions and the unresolved evidence
policy are preserved. Human/Lead review remains separate; no merge or deployment.

Reverting the code reopens the export/cancel exposure. Preserve all export/audit
evidence and do not reset counters, release exported links or fabricate payment
state as a workaround. There is no schema/data downgrade; any actual historical
repair or future void process requires separate evidence and authorization.
