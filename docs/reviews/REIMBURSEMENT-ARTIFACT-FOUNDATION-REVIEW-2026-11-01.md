# RB04 architecture and policy review — 1 November 2026

Read-only source review of PR #98 source `eb02cd2ef0323734ed8f8cac47f8975cdb925d4a`
in `/workspace/bidit-rb04-20261101`. AI-assisted investigation; no source edits,
runtime reproduction, test execution, provider activity, production access or
GitHub writes were performed by this reviewer. This is engineering advice, not
human/Lead approval of a financial or retention policy.

## Recommendation for the current development slice

Prioritize EX17, the independently actionable direct-terminal reimbursement
boundary, while preserving RB04 as the next designed artifact foundation. This
does not mean all RB04 engineering is prohibited: Decisions §33 expressly permits
preparing its foundation and synthetic proofs. It means a live durable-file
feature would need an explicit retention/download/replacement contract, and an
unused internal framework should not be called a completed product repair.

Decisions §33 lines 1437–1441 expressly leaves EX17 unblocked. Lines 1452–1459
leave artifact retention/legal hold, the audience for bank-bearing downloads,
replacement, purge and unknown external disposition open. AI Engineering Policy
§5 prevents engineering from supplying those business/legal choices. Do not pause
unrelated authorized bug fixes while these choices remain visible.

## Source-backed gaps and existing protections

* `backend/app/api/routes/reimbursements.py:206–241` renders CSV, increments the
  export marker, writes an audit, commits at line 232, then returns unretained
  bytes. A lost response has no stored artifact/receipt from which to retrieve
  the exact file. There is no export command key or frozen source manifest.
* The SEPA route at lines 244–294 has the same gap, generating a new MsgId from
  count/randomness before render and committing at line 284. A repeated new
  command can create a different instruction identity.
* Those routes retain router-level EXPENSE_APPROVE, module checks, tenant-scoped
  batch loading and a batch row lock. These must remain; the review does not
  establish broken tenant isolation in those routes.
* `backend/app/services/reimbursement.py:100–130` refuses cancelled batches and
  requires acknowledged re-export, but does not refuse paid batches or validate
  reviewed batch/report versions. `record_export` changes export markers without
  incrementing the batch's optimistic version. A future stale-input contract
  therefore must account for the export marker/count as well as `version`.
* `batch_reports` at lines 162–170 orders by employee name without a stable ID tie
  break or report row locks. It is suitable for display, not by itself a frozen
  command source. Artifact creation should follow membership → batch → ordered
  report locks, with all reviewed facts checked inside that boundary.
* SEPA at lines 430–468 can acknowledge missing-IBAN employees and produce a
  subset; later payment records every linked report. That contradiction is
  already documented; this review did not execute it. All-ready versus explicit
  child allocation remains the separate EX22/RB05 decision, not an implicit RB04
  policy change.

## Reuse mechanics, not an incompatible export lifecycle

Existing `WorkspaceExport` and `ArchiveExport` are intentionally separate row
types for authenticated live-workspace versus public ex-client email-redemption
doors. They provide single-use email links, not authenticated repeatable exact-byte
GET. Reusing those rows or token purposes would import an unrelated security and
lifecycle contract.

`backend/app/services/workspace_export.py:425–480` reads current rows when its
worker runs, stores an `exports` object and assigns link expiry. It does not freeze
the reimbursement source under its original locks. Copying that implementation
would not establish immutable payment-instruction identity.

`backend/app/services/export_artefacts.py:45–49,88–147` purges only those two row
types, upon link expiry or a one-day downloaded grace, honors legal hold and
deletes registry metadata with bytes. `load_verified` at lines 61–85 contains a
useful bounded-size/digest check, but hardcodes `documents.EXPORTS`. The `exports`
prefix and this expiry must not be assigned to payment evidence by convenience.
ADR-0008's addendum explicitly requires a derived class's expiry to be written
down. The generic absence-of-retention-row keep-forever default is not a substitute
for deciding this new evidence promise.

Object storage remains the native byte home under ADR-0008 and
`services/documents.py`. Avoid a new in-DB file/base64 staging implementation just
to obtain database atomicity; that reintroduces blob storage and does not solve
the retention decision. Reuse `documents.store/load/digest`, threadpool I/O,
tenant/content-addressed keys and document registry, with a separately named
reimbursement class/purpose once its lifecycle is reviewed. No new provider is
required.

The durable database job queue already supplies unique `(org,kind,key)` intent,
claim/retry/lease handling and `commit=False` enqueue. `jobs.run_once` rolls back
handler writes before recording failure, so assigning `artifact.status='failed'`
and then raising does not persist that status. Derive recoverable state from the
linked job or design an explicit persisted transition; never claim a failure mark
was committed merely because the handler assigned it.

## Minimal proposed active design after the policy review

1. Add a dedicated tenant artifact row, rather than a second command executor:
   batch ID, actor, reference to the existing ExpenseCommand identity, format /
   reviewed profile version, frozen source manifest, filename/content type,
   row/transfer count, exact Decimal/currency/FX facts, fixed render time/MsgId,
   expected SHA-256 and size, linked job, verified-ready evidence and explicit
   lifecycle/disposition fields. Do not write guessed legacy artifact rows.
2. Reuse `expense_commands.command_key`, `lock_actor` and `lookup` with the
   existing `(org,employee,key)` uniqueness. Add the relevant operation to the
   existing reimbursement command/status contract and audit mapping; a new export
   key namespace would allow collisions with other consequential expense actions.
   Same key + exact inputs returns its original receipt before new-export guards;
   changed payload conflicts. Current permission/membership still applies to
   recovery and download. An old receipt is historical evidence, not permission
   to create a new instruction after pay/cancel.
3. Within one database transaction, freeze and validate source facts, capture the
   exact rendered hash/size, record export marker/audit/receipt and enqueue keyed
   storage work with `commit=False`. Make all failure paths fail closed. Work may
   render from the frozen manifest only; include renderer/profile identity and
   compare to the original expected hash so a changed renderer cannot silently
   replace the original bytes under the same key.
4. Put no object before the artifact/job transaction commits. A worker can then
   store only from an already committed manifest. A crash after object write but
   before ready leaves an existing row naming its exact expected object, allowing
   retry/reconciliation. This narrows orphan cases but is not database/object
   atomicity. No ready state until stored bytes, size and hash were independently
   verified. Existing storage protocol has no general object-listing/GC primitive;
   do not claim an existing global orphan sweep that source does not implement.
5. Separate creation, authorized status, and bounded exact-byte GET. GET verifies
   source object identity and has no new marker, message ID, payment, command,
   creation audit or job. Server response success is not browser disk persistence
   or bank acceptance. Missing/corrupt/purged objects return explicit unavailable
   recovery states; do not regenerate from current business rows.
6. Replacement/regeneration must be a reviewed new command with predecessor and
   disposition evidence, not `confirm_reexport` silently reused. Partial-payment
   allocation, paid/cancelled new-instruction eligibility and bank-specific
   acceptance remain separately reviewed rules.

## Migration and regression prerequisites

The source has one migration head `f0b2d4e6a8c1`. Any artifact migration must be
additive after that head, preserve all prior history, import/register its model
in `models/__init__.py` and `core/tenant.py`, and apply ENABLE/FORCE RLS with the
existing `app.current_org` predicate. Use an explicit same-tenant composite batch
FK, rather than an org column plus unconstrained cross-tenant batch reference.
Choose deletion/retention behavior intentionally; an automatic batch cascade must
not destroy retained instruction evidence. Downgrade should refuse retained
evidence rather than erase it, with documented application rollback keeping the
additive table. Maintain migration security/preflight/postcondition guards.

Meaningful proofs include:

* Actual native PostgreSQL same-key races, different actors/keys against the same
  source, stale source/export marker, pay/cancel/create races, non-superuser RLS,
  composite FK violation and exact transaction rollback after audit/job failure.
* Synthetic worker storage exceptions, crash-after-put before ready, restart
  retry, missing/truncated/oversize/wrong-hash object, dead job/readback and no
  duplicate marker/receipt/audit/job. A seeded bad implementation must make the
  regression fail.
* Retention/legal-hold/purge tests after the approved lifecycle is known, including
  shared content-addressed object references: purging one artifact must not delete
  an identical object still retained by another live artifact.
* A real browser/API/PG saved-file test that arms a download, parses actual saved
  CSV/XML, compares committed hash/size/identities, refreshes and retrieves the
  identical object twice. Mocked HTTP success does not prove this.
* Existing limits at 1/100/1000 admitted rows, measured wall time/CPU/RSS/query
  count/output bytes/queue completion and unchanged bundle/security limits.

Read-only inventory of current legacy counters is useful but cannot supply
missing historic bytes, MsgId provenance, bank submission, confirmation or
settlement. Preserve those distinctions in the backlog and product wording.
