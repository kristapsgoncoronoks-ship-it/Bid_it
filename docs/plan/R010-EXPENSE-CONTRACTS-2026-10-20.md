# R010 expense command contracts

Base: main7608c1e6335a464476d548bfbb28d3fda3ba499f.
Cumulative work: R010-A-EX01-P01r2, EX03-P01r2, EX02-P01r2;
B00/F01/F12/O03/O05/O06/O07/A01/A02.

## RED checkpoint

PR65 commit7d855aeb4e5d5689ddc7ef37d7363c62f2618cfe.
CI39365148629/backend118440176305 ran:
python -m pytest tests/test_r010_expense_commands.py -q
Result: eight intended assertion failures in9.48s. USD became EUR;
exact replay duplicated a candidate; changed payload under the same key
succeeded; unknown currency was guessed; EUR+USD became EUR20;
report/removal retries returned404; oversize upload used read(-1).
These are API/SQLite contracts. New PostgreSQL/browser evidence is pending.

## Decisions, alternatives and risks

Reuse the installed ORM, PostgreSQL, bounded file read/security/parser,
canonical expense FX representation, audit and recycle bin. Two small domain
tables record commands and exact imports. They are not another Function Studio
execution engine; the capability catalogue should link to these API contracts.

Command grain: active organization + employee + caller key, across operation
kinds. Same key with changed kind/payload conflicts. A membership row lock
serializes an employee's commands and rechecks live expense authority/module
state; uniqueness is the database backstop. Candidate assignment/bin/restore
writers take row locks and advance versions. Restore advances the epoch, so
an old removal replay cannot remove a restored candidate.

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

## Migration and rollback

Additive tenant tables, nullable import provenance and version default1. No
historical money/account/currency/FX/source identity is rewritten or inferred.
One Alembic head; models, tenant guard and PostgreSQL RLS register together.
New unique indexes may lock tables. Downgrade refuses persisted command/batch
evidence. Retain additive schema on application rollback and disable candidate
writers that ignore the locking/epoch contract. Do not discard receipts to
force downgrade. No tax, bank-balance, approval or retention policy changes.

## Verification still required

Eight regression tests must turn green. Run PostgreSQL races, raw RLS/unique
constraint assertions, audit fault rollback, restore/replay, actual browser
creation/readback and failure rendering separately. Existing gates stay active.
Image/history security dispositions remain release blockers. AI-assisted
implementation/tests/docs require observed CI and human review.
