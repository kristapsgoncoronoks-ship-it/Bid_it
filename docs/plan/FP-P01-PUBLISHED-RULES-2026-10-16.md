# FP-P01 / FS16-01–03 — Reviewed, validated published automation

AI-assisted implementation and verification; human review and exact-head CI pending.
Sources: [uploaded report reconciliation](../reviews/RECONCILIATION-2026-10-16.md).
No new engine, framework, broker or database migration.

## Published execution and cutover

The sweep now loads the tenant-scoped immutable snapshot named by published_version,
including trigger, conditions, ordered actions, name and fire policy. Editing the
mutable draft cannot affect that version. Revert validates the old snapshot and
publishes a new append-only version, including its original name. Existing successful
once-per-record history remains rule/record scoped across versions; publication does
not re-enroll completed records.

New snapshots carry definition_version 2. For legacy snapshots without this marker,
execution compares the historical snapshot with the mutable definition. A mismatch
blocks the rule, logs an explicit failed/blocked-definition outcome and permits other
valid rules to proceed. It does not silently choose either historic policy. An owner
must review and explicitly publish the intended draft (or revert a reviewed version).
A read-only per-rule preflight endpoint and editor warning expose reconciliation.
Unsupported/missing/invalid published definitions fail closed. No history is rewritten
and there is no blanket migration that silently disables or republishes customer rules.

Before rollout, owners must inspect published rules with the preflight endpoint and
review any legacy mismatch. Paired API/UI rollout is required. Legacy API writers
must fetch the rule revision and supply it as If-Match for edit, publish, status or
revert; missing revision returns 428, stale revision 409 with no mutation. Hashes bind
the reviewed definition/status/published version. PostgreSQL row locking serializes
writers and per-rule sweep evaluation; snapshots retain the unique version constraint.
After an uncertain response, reread the rule/version before deciding what to do.
Rollback to the previous API reintroduces draft-driven execution: pause automations
first and review intended definitions rather than silently restoring unsafe behavior.

## Definition validation

Validation runs at create/edit, publish/revert and execution, not only in the UI.
Conditions require exact operator arity, supported trigger fields and operand types,
finite numbers, boolean roots, at most 16 nesting levels, 256 nodes, 2,048 characters
per string and 16 KiB serialized condition bytes. Missing/null ordered values do not
match. Ordered numeric comparison uses Decimal rather than rounding through float;
this is condition comparison, not a new currency-conversion or accounting policy.

Actions are a closed discriminator with no arbitrary URL/extra fields, at most ten
per definition, subject at most 200 characters and body at most 16,000. Template
references must belong to the trigger context. Cooldown is an integer 1–8,760 hours.
Malformed legacy definitions fail with a visible validation outcome, not IndexError
or TypeError that aborts every neighboring rule. Financial authorization/services and
SETTINGS_MANAGE remain unchanged.

## Editor preservation and action contracts

Ambiguous numeric-looking strings such as `001`, list members containing commas such
as a single `Smith, Ltd`, mixed lists and nested expressions use the existing raw
JSON editor. Unrelated name/action edits preserve the original condition exactly.
Simple rows remain available for representable definitions. Local edits remain in
the form after a stale-save error; reload/review before submitting a fresh revision.
Mutation pending continues through query invalidation/readback.

| Control | Intended result and recovery | Maintenance prompt |
|---|---|---|
| Save changes | Validate the draft and change only the reviewed revision; show conflict without discarding local input. | Preserve typed/raw conditions, reject unsupported fields/operands, bind editor-open revision, never silently merge stale edits. |
| Publish / Enable / Disable / Revert | Mutate one reviewed revision; publish immutable validated history, or explain conflict/legacy reconciliation. | Lock the tenant rule before checking revision; race two publishers and edit versus publish in PostgreSQL; retain once-per-record history. |
| Edit / readiness preflight | Show whether legacy execution needs reconciliation without choosing a business policy. | Compare legacy definitions exactly, fail closed on missing/unsupported snapshots, and permit independent valid rules to run. |

## Evidence and remaining work

Twenty new validator regressions failed against the old implementation (including
malformed shapes, unsupported fields, bad action types and float precision), then
passed. The focused final group passed **41 tests**, including real migrated
non-superuser PostgreSQL publisher/publisher and publisher/editor races. Each race
produced one 200 and one 409, with no duplicate version. Runtime tests pin original
v1 actions and days>7 after the draft changes to days>14 for a ten-day record, block
legacy mismatches until explicit publication, preserve historical JSON and isolate
an invalid rule from a valid neighbor. Three existing tenant-parity probes passed.

Nine browser tests passed, including exact raw-condition preservation for the three
cases above, If-Match publication and conflict/local-input recovery. These browser
tests mock API branches; PostgreSQL tests supply the real concurrency evidence.
Whole-app mypy, Ruff/format and frontend lint/build/bundle checks passed.

This does not close FS16-04 preview eligibility/cap truth or FS16-05 durable email
outcomes/partial-action retries. Those remain distinct queued repairs; the current
mailer behavior must not be certified as durable delivery by this change. Full branch
CI, image advisories, human control review and private-history verification also remain.
