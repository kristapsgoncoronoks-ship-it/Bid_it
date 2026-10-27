# EX20 — recoverable operator payment recording

27 October 2026. This candidate follows PR #86 at
`2c51a4a6555e7dcd9c81a4de8cbf4f4f7c35d55b`. It implements O05-RB03 / EX20's
technical payment-record command, evidence projection and recovery. Required
reference/method/date/attestation policy remains decision 33; bank/provider
confirmation, full EX19 cancellation recovery and EX21/22 exports remain separate.
[Parent ordered contracts](REIMBURSEMENT-RELIABILITY-2026-10-27.md).

## Result and financial boundary

**Record payment records an authenticated operator's statement. It does not send
money or confirm bank processing.** New committed records expose
`operator_recorded`, actor ID and server recording time with bank confirmation
`unknown`. Older paid batches without this receipt remain `legacy_unknown`.
Contradictory/duplicate receipt metadata is `unavailable`, rather than a newest-row
guess. An open unrecorded batch is `not_recorded`. No caller can assert provider
confirmation. The existing paid state/event and optional reference/method remain;
the timestamp describes recording, not a newly invented bank payment date.

The existing batch lock and maker-checker control remain. New keyed requests
freeze batch ID/version, exact reviewed EUR total, every linked report's observed
version, optional reference/method and explicit override flag. The shared
`ExpenseCommand` tenant/actor/key identity now supports `reimbursement_paid`;
cross-batch, operation or payload reuse conflicts. Lookup precedes current-state
rejection so a committed retry returns its original frozen result without another
record. No-key legacy calls retain their state/version refusal behavior and gain
a fresh provenance receipt, but do not gain client-key outcome recovery.

The transaction takes membership, batch and deterministically ordered report
locks before any override/main audit. It refreshes and checks authority, current
versions, exact report set, eligible state and absence of contradictory payment
facts. Locked Decimal totals must equal the stored and reviewed amount; it no
longer silently overwrites the saved batch total from changed reports. Empty,
terminal, missing-FX, stale or contradictory sets refuse without writes. A valid
record advances batch/report versions once and commits the terminal state,
immutable receipt, required ordinary/override audit and strict keyed webhook/job
intent together. The public event name/payload remain compatible; queued intent
is not external delivery. Global advisory defaults are unchanged.

Current APPROVE/module/membership, maker-checker and explicit audited platform
admin override are preserved. The UI does not introduce an override control or
new required business field. Both older expense-command lookup aliases also
require APPROVE for payment receipts; the reimbursement lookup explicitly permits
create/payment operations only. Another approver may read minimal batch evidence,
but cannot read another actor's command key/result through keyed lookup.

Historical create receipts did not store report versions. Their new readonly
version field remains null/unknown; current ORM detail reads supply real versions
and payment preparation requires positive current values. Server recording times
are normalized to UTC consistently across receipt/current SQLite/PostgreSQL views.
Membership authority is linearized by its lock; no new immediate module-disable
or global-user/session revocation policy for already-started requests is claimed.

## Confirmed related lock defect

Native HTTP transactions reproduced a cycle in the existing team role-update
route: an expense command held its membership row while waiting for audit; the
role update held the tenant audit lock while waiting to flush that membership.
PostgreSQL detected the deadlock and the role update returned 500.

The bounded repair locks and refreshes the target membership before team
mutation/audit. The same controlled schedule now returns 201/200, with no audit
lock held while waiting, valid audit/session behavior and one financial command.
Last-owner/approver rules, permissions and session revocation policy are unchanged.
This is a confirmed synthetic route defect, not a claimed production incident.

## Interface and recovery

The UI first obtains a bounded authorized current detail and checks it against
the displayed batch. Confirmation names the amount, report count, stored method
and optional reference and explains the recording boundary. Inputs are captured
before asynchronous work. A per-batch key/digest/payload survives reload and
navigation; identity/generation/abort guards fence obsolete continuations.

Unknown outcomes retain their reference and block that batch's payment, cancel,
export and reference editing. **Check payment outcome** performs GET only. A
missing receipt does not prove rollback: an explicit **Retry original payment**
checks again and resends only the same frozen key/payload if still missing.
Receipt operation/key/target/actor, amount/report/version facts and recording
instants are validated separately from current state/list readback. Only then is
the saved reference cleared. Malformed, denied, failed or native 15-second reads
and writes cannot silently enable a replacement financial command.

Confirmation ownership also covers cancellation and deferred export warnings:
one batch's pending review cannot replace another batch's unresolved dialog.
Named batch groups and existing local table/dialog scrolling retain all facts
at enlarged text. No shared modal, style, dependency or visual baseline changes.
The late nullable historical-version TypeScript correction emits byte-identical
JavaScript; pre-correction focused results remain historical and current-source
checks are separately bound.

| Action / prompt | Description and maintenance assignment |
| --- | --- |
| Record payment — `EX20-PAY-P01 r2` | Read and review current batch facts, preserve optional reference/method and maker-checker, freeze identity/version/total/report set/key, then commit the operator record exactly once under real locks. Refuse stale/changed/terminal/FX contradictions without rewriting the amount or inventing bank confirmation. Verify API/PG/browser readback and strict audit/intent rollback. |
| Confirm / Cancel review — `EX20-CONFIRM-P01 r1` | Explain the exact existing-payment assertion. One confirmation owner must reserve the dialog through review; cancellation sends no payment POST and releases its controls. Verify two-batch review/cancel ordering and native keyboard access to both footer actions at narrow/enlarged text. |
| Check payment outcome — `EX20-LOOKUP-P01 r1` | Use the original actor/tenant/key in GET only. Validate the original receipt and current financial state before forgetting it. A missing, malformed, failed or unauthorized result must not become a new POST or a confirmed bank outcome. |
| Retry original payment — `EX20-RETRY-P01 r1` | Offer only for a retained original request after a missing-result check; recheck first and reuse its exact key/payload. Exercise lost responses, 429/5xx/offline/native timeout, navigation, reload and replacement identities; compare full receipt/audit/outbox state after replay. |
| Refresh batch status — `EX20-READBACK-P01 r1` | Refresh authorized current data and minimal provenance without repeating the write. Keep any unresolved keyed request until its own receipt/current/list checks complete. Do not use a current paid flag alone as command attribution or bank evidence. |
| Existing cancel/export/reference controls — `EX20-INTERLOCK-P01 r1` | Preserve EX19 export guards and export acknowledgement flags. Exclude conflicting same-batch operations throughout payment uncertainty, and preserve confirmation ownership across batches. Keep EX21/22 artifact acceptance separately open. |

Session storage supports the retained tab's reload/navigation; recovery after
losing the original key by closing the tab/browser is not qualified. A paid
legacy record stays unknown unless authoritative evidence exists.

## Additive index and read cost

Migration `a9c3e7f1b5d0`, parent `d4e6f8a01223`, adds only the non-unique
`expense_commands(org_id, operation, target_id)` read index. It adds no table,
column, uniqueness, backfill, evidence reinterpretation or historical repair.
PostgreSQL builds/drops it concurrently; SQLite uses its ordinary index operation.
An already-present same-name index is refused, including an interrupted invalid
build. One migration writer, production disk/memory/long-transaction planning and
operator reconciliation after interruption remain required before deployment.

Batch pages select minimal grouped receipt metadata for their bounded IDs, not
large frozen JSON or another actor's key. Report counts use the existing indexed
link in a correlated scalar; actual 1/20/100-row pages use **three domain SELECTs
including receipts**. Native plans choose the new index without hints and return
517 / 9,467 / 46,998 bytes in the synthetic probe. No N+1 or full-result parsing is
introduced for provenance. The fixture has 200 batches, 2,000 reports and 10,102
receipts across two organizations; deliberately inconsistent seeded recording
times remain unavailable evidence, not successful payment acceptance.

Empty PostgreSQL/SQLite upgrades, representative upgrade/downgrade/re-upgrade,
real invalid concurrent-index refusal and SQLite model parity pass. Full financial,
receipt, audit, delivery and job row hashes remain identical throughout the native
migration probe. Downgrade removes only the new index. No production migration ran.

**Native PostgreSQL global metadata parity is not clean:** both the untouched
`2c51a4a` baseline and candidate report the same eight FK comparison operations
(four remove/add pairs). Migration `c4d6e8f0a2b4` intentionally uses PostgreSQL
targeted `SET NULL(column)` while generic ORM metadata says `SET NULL`. No index
drift is reported; the exact new-index catalog/plan/data-preservation evidence
qualifies this delta separately. No FK policy or global gate was altered, and no
whole-PostgreSQL-parity pass is claimed.

**Follow-up `DB-PG-PARITY-01-P01 r1`:** review dialect-aware metadata comparison for
these four targeted-null FKs against the preserved candidate/baseline diagnostics.
Prove actual project/issuer deletion preserves the tenant key and remaining
references on PostgreSQL, retain SQLite compatibility, and propose the smallest
reviewable model/comparator correction. Do not replace targeted nulling with
tenant-key nulling, hide other drift, regenerate truth without semantic review,
or change a gate merely to suppress this diagnostic.

## Qualification actually executed

- **117 API tests passed**, including 38 new payment cases. Existing creation,
  R010, SEPA, export and maker-checker controls remain green. The unchanged old app
  fails the amount-drift case: it accepts 200 when changed EUR 0.31 reports belong
  to a saved EUR 0.30 batch; the candidate returns typed no-write refusal.
- **30 real PostgreSQL payout tests passed**, including 12 new cases: repeated
  same/different key/actor pays, pay/cancel in both orders, post-wait authority/
  report/version/amount checks, and override-versus-report-decision audit order.
  Distinct PIDs/observed blocking and complete committed snapshots establish the
  original route locks. Isolated removal of report locking and early override
  audit controls fail as intended, without editing candidate files.
- Related team ordering: **two registered PostgreSQL cases and 36 compatibility
  tests passed** after the native failure-first deadlock reproduction.
- **61 focused browser tests passed** (29 new payment, 18 retained creation and
  14 cancellation); final default suite **901 passed**, zero retries/skips/flakes,
  709.29 seconds. Focused cases overlap that total. An old frontend control posts
  before the reviewed confirmation, reproducing the intended guard failure.
- Actual unmocked browser → Vite → API → PostgreSQL: owner UI creation, maker 403
  with identical state, independent checker UI record for EUR 0.30/null reference,
  report versions 2→3, one new payment receipt/audit/queued delivery/job, validated
  receipt/current/list readback, no-write same-key replay/conflict and cross-actor
  lookup refusal. Legacy unknown basis survives reload. Approved/legacy states
  were explicitly ORM-seeded. No actual network-loss fault was injected here;
  browser fault handling is separately mocked.
- Actual 390px/root32px page and null/200-character confirmation probes retain
  native scrolling/keyboard access to both footer actions; zero probe payment
  POSTs and unchanged financial rows. Suspected below-panel text was underlying
  page content through the backdrop, not a reproduced dialog overflow defect.
  This is not whole-device, screen-reader or WCAG certification.
- Type/lint/build, mypy, Ruff, generated API/docs/migration/ERD/PG-registration
  checks pass. Bundle remains **437.6 kB raw / 129.0 kB gzip** within 460/135.
  Impeccable source scan returns no primary findings but is regex-only for TSX;
  no parsed-element/contrast certification is inferred. No goldens changed.

[Source-bound receipt](../security/evidence/REIMBURSEMENT-PAYMENT-COMMAND-2026-10-27.json)
and local artifacts under `/workspace/artifacts/reimbursement-payment-2026-10-27/`
retain attempts, controls, resource observations and limitations. Shared-host
pytest/Node timings/RSS exclude other processes and do not establish controlled
cold/warm improvement, modest-hardware capacity or production sizing. Initial
fixture/collection errors, timezone correction, provisional duplicate migration
ID corrected before apply, and type-only source timing are classified separately.

The preceding PR #86's repaired head has completed green hosted CI, including
872 browser cases without retries; original flaky evidence is preserved. Those
checks qualify its source only. Fresh complete hosted CI and human/Lead review
remain required for this new candidate; no merge or deployment is implied.

## Review, rollback and remaining work

AI assistance produced implementation/tests/docs and independent adversarial
review. Reusing existing receipts avoids duplicate provenance columns/frameworks;
client-only pending state cannot establish database exclusion or lost-response
recovery. Objections about lock/audit order, aliases, history, timestamps, bounded
metadata, dialog ownership and native scrolling were resolved or explicitly
qualified. Human/Lead understanding and approval remain pending.

Preserve all receipts, report links, audit/outbox rows and original operator keys
during rollback. Dropping the new index changes read performance only. A backend
compatibility rollback **must retain APPROVE authorization for
`reimbursement_paid` in both generic expense receipt aliases**; older code did
not know this operation and could otherwise remove that protection for retained
receipts. Do not roll back to an unaware backend or erase receipts to make it fit.
Coordinate new status/schema clients and stop new records while resolving unknown
commands. A code revert reopens amount/locking/recovery defects; no automatic
payment, reversal, release, history repair or compensation is authorized.

EX19 full reason/version/receipt recovery, EX21/22 artifacts, EX17 direct-terminal
closure, owner-required payment policy, external B00/F10/readiness controls,
accountant acceptance and production resource qualification remain open. This
operator-record slice is not the whole reimbursement or commercial release.
