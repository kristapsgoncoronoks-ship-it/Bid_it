# R010 expense command contracts

Base: main `7608c1e6335a464476d548bfbb28d3fda3ba499f`.
Draft implementation: [PR65](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/pull/65).
Cumulative work: R010-A-EX01-P01 r2, EX03-P01 r2, EX02-P01 r2;
B00/F01/F12/O03/O05/O06/O07/A01/A02. No deployment or merge is authorized.

## Observed test checkpoints

The test-first commit `7d855aeb4e5d5689ddc7ef37d7363c62f2618cfe`
ran eight intended RED assertions in 9.48s:
[CI39365148629/backend118440176305](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39365148629/job/118440176305).
USD became EUR; exact replay duplicated a candidate; changed payload under the
same key succeeded; unknown currency was guessed; EUR+USD became EUR20;
report/removal retries returned404; oversize upload used read(-1).

By commit `15e3af494efe983c50395cb7d55c9ed735edbdf9`, those API
contracts pass. [CI39368929800](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39368929800)
also passed 66 native PostgreSQL tests, six existing growth tests and two
real browser/PostgreSQL journeys. The focused API suite was 14 passed / one
failed: its new platform-override regression exposed a live-authority recheck
which discarded the existing global override. The next candidate preserves
that flag while refreshing membership role/approver/status; its result must be
verified, not inferred from this document.

The subsequent candidate adds independent database uniqueness/raw RLS/
receipt-retaining downgrade-refusal coverage and explicit recovery-button
browser cases. Final SHA-bound results and remaining gates belong in the PR
and cumulative register. A changed commit requires its own passing checks.

## Decisions, alternatives and risks

Reuse the installed ORM, PostgreSQL, bounded file read/security/parser,
canonical expense FX representation, audit and recycle bin. Two small domain
tables record commands and exact imports. They are not another Function Studio
execution engine. Link the existing capability/action planning catalogue to
these API contracts. No executable expense Function Studio capability ID was
found in this bounded source inspection; do not invent one or enable an
unsupported Studio executor.

Command grain: active organization + employee + caller key, across operation
kinds. Same key with changed kind/payload conflicts. A membership row lock
serializes an employee's commands and rechecks live expense authority/module
state; uniqueness is the database backstop. Explicit keyed selected-report and
removal requests require observed transaction versions. Candidate
assignment/bin/restore writers take row locks and advance versions. Restore
advances the epoch, so an old removal replay cannot remove a restored candidate.

Exact source identity: organization + employee + explicit account reference +
file SHA256. An account reference is an import-scope label, not verified account
ownership/balance. Equal rows at different source ordinals remain distinct.
Different/overlapping files are not fuzzy-deduplicated. Each row retains source
currency or explicit confirmation; unknown/contradictory currency is refused.
New import clients and fixtures must supply account reference and currency
confirmation when the source omits it.

Keyless legacy selected/import/remove clients get a deterministic operation
identity; a new identical intention requires an explicit client key. Legacy
manual draft creation retains its new-draft-per-request behavior, with a
server-generated receipt key; safe browser retry is not claimed for that
unreviewed form. Existing legacy statement rows keep unknown provenance and
must be reviewed rather than assigned a guessed currency.

Writes, receipt and audit commit together. No external provider is called by
these operations. Status lookup is scoped to the current employee/organization.
It returns a command snapshot, not current approval state. Exact import replay
reports current available/assigned/binned/unavailable counts and never recreates
removed rows. Recovery uses permitted existing bin restore. Explicit reimport
after deletion is unsupported until an approved policy exists.

Alternative: pending buttons plus file hash only. Rejected: no durable
lost-response recovery, changed-payload protection or restore epoch.
Alternative: generic new command framework. Deferred: domain records and the
existing transaction patterns suffice.
Strongest objection: per-employee serialization and receipt storage add cost.
Bound input/selection sizes and measure lock wait/CPU/RSS/query count before
changing isolation or adding caches. No performance improvement is claimed.

## Action contracts

| Catalogue action | Visible control | Observable result |
| --- | --- | --- |
| R010-A-EX01 | Import bank statement | Bounded PDF/CSV parse, source currency/provenance, exact-source receipt and candidates, then actual inbox read. |
| R010-A-EX03 | Create report from selection | One draft/report/items/assignments/receipt/audit with exact currency and current versions; actual readback. |
| R010-A-EX02 | remove / confirmation Remove | One versioned recoverable bin operation; assignment conflicts; restore through permitted existing Trash. |
| R010-A-EX04 | Check previous import outcome | Scoped GET receipt then current inbox read; never imports. |
| R010-A-EX05 | Check previous report outcome | Scoped GET receipt then current report/list read; never creates a report. |
| R010-A-EX06 | Retry original report request | Status lookup first; only a definitive404 allows one supported same-key original-payload request. |
| R010-A-EX07 | Check previous removal outcome | GET old receipt, refresh current inbox, never removes a restored row. |
| R010-A-EX08 | Retry loading inbox | Scoped read only; loading/error/empty remain distinct. |

EX04–EX08 each map to their own R010-A-EXnn-P01 r1 verification/repair
assignment in the cumulative review register. Full maintainer prompts stay
there; concise purpose, blockers and next steps remain in the product.
Conditional controls and their permission/identity matrices remain explicit
coverage work, not a static-tag denominator.

## Migration and rollback

Additive tenant tables, nullable import provenance and version default1. No
historical money/account/currency/FX/source identity is rewritten or inferred.
One Alembic head; models, tenant guard and PostgreSQL RLS register together.
New unique indexes may lock tables. Downgrade refuses persisted command/batch
evidence. Retain additive schema on application rollback and disable candidate
writers that ignore the locking/epoch contract. Do not discard receipts to
force downgrade. No tax, bank-balance, approval or retention policy changes.

## Evidence boundaries

API/SQLite failure and permission tests are distinct from native PostgreSQL
race/constraint/RLS guarantees. The live browser suite calls the actual API
and verifies committed records with independent psql. A lost-response test
commits through route.fetch before aborting only transport. Mocked offline/
rate-limit rendering cases do not prove a financial completion.

The resource wrapper is dev/test only, without new dependencies. CPU/RSS
cover the API process, not PostgreSQL/Chrome. Sparse synthetic CSV samples
are observations, not before/after gains or a2-vCPU/4-GiB capacity claim.
Queue and OCR/PDF performance remain unmeasured for this run.

Keep existing CI gates. Final-image/history security and any intended visual
baseline differences need their normal disposition; this branch does not
waive them. AI-assisted implementation/tests/docs require observed CI and
human review.
