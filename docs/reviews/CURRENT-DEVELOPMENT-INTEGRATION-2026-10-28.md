# Current development integration — 28 October 2026

This is a reviewable integration candidate, assembled in an isolated worktree.
Main remains `90891d8d52cf5aefc4467e3fa7f6070c042acec2`; no production merge,
settings activation or Hostinger deployment is performed. The candidate combines
current planning with the OCR, interface, finance-command and CI-security work
already present on the development stack. Human/Lead acceptance of inherited
scope remains required.

## Source and migration reconciliation

Planning source `24abfa67bc0a05633970a449196c9e2a9f4e4cda` and repaired completion
source `c66245ce0d2ab6acd31fe94dc3f91a444c03f8b5` are preserved through local merge
ancestry. The latter includes payment PR #87, report intake and the later UI and
security repairs. Nine initial conflicts were reconciled without dropping either
feature set. Both current backlog history and the added planning preamble remain.
The initial broad documentation parser was rejected, the four root-owned files
restored from Git conflict stages, and final line-based resolutions verified.

Existing marker `e5f7091a2345` is preserved byte-for-byte from the earlier #80
candidate, with its original planning/OCR parents. Payment index revision
`a9c3e7f1b5d0` is unchanged. New marker `f0b2d4e6a8c1` joins those two heads;
its upgrade and downgrade bodies contain no DDL or data actions. The final graph
has **155 revisions and one head**. No prior migration is reparented or rewritten.

The migration matrix exercises empty, main, planning, OCR, payment, earlier
integration and multi-head starting states on SQLite/PostgreSQL. Marker-only
roundtrips preserve selected rows and inspected catalog definitions; additive
feature removal is not application rollback. Invalid or unrecorded payment-index
states refuse and require explicit fixture recovery. Ordinary upgrade/downgrade
paths retain their existing security controls.

## PostgreSQL comparison and deletion integrity

The four DB-PG-PARITY-01 differences were already present on PR #87's unchanged
parent. They express intended PostgreSQL column-specific SET NULL behavior,
not broken constraints. Comparison-only copied metadata recognizes the exact
existing forms after checking both database and portable model contracts.
Alembic still compares the entire schema; no drift operation is filtered.
CLI/programmatic check and autogenerate are qualified separately from ordinary
migration commands, which do not invoke the helper.

The correction is also isolated in [draft PR #93](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/pull/93),
based on PR #87 for independent review. Native regressions cover all four real project/issuer deletions, tenant
identifiers, only permitted nullable references, RLS and cross-tenant FK refusal.
Wrong targeted behavior and unrelated FK/index/column/type changes remain errors.
No financial row, FK policy, model definition or production schema is repaired.

## Interface and contract preservation

Planning Board/Calendar, queued notification/provider truth, version recovery,
identity fences and native controls are retained, with the two newer contrast
improvements. The default functional suite is the exact union: **929 cases in
83 specs**. Existing 901 cases and the added planning cases remain; dependencies,
lockfile, assertions and visual goldens are unchanged.

All four calendar/capture probes are retained. Metadata/ERD contains 119 tables,
112 tenant models; generated OpenAPI matches the merged 417-path contract.
Existing resource limits remain with the calendar-specific response bound.
Worker registrations include calendar and expense/capture capabilities together.
Current security pins and audited image/provenance/refusal behavior are preserved;
calendar worker handling extends the existing deployment quiescence controls.

The result-only completion-gate repair is included. Only fixed child result
statuses enter runner environments, excluding arbitrary upstream outputs before
GitHub logs them. All original check contexts, baseline and deployment boundaries
remain. Required branch settings are still an owner/admin action.

## Executed qualification and limits

Exact invocations, hashes, counts and attempt history are in the
[machine receipt](evidence/CURRENT-DEVELOPMENT-INTEGRATION-2026-10-28.json) and
`/workspace/artifacts/current-integration-2026-10-28/`.

- All 929 default functional browser cases passed without retries, skips or
  flakes. The focused 37 and 19 proxy cases are separate invocations, not added
  to that coverage count. Lint/build and the unchanged bundle limits pass.
- The registered ordinary PostgreSQL cohort passed 241 cases across 44 modules.
  Its source pins remained stable. A later helper-only local variable rename is
  byte-proven equivalent and independently requalified natively.
- Migration matrix: 22 passes and three explicit SQLite/native-only skips;
  all 12 PostgreSQL matrix cases execute. A separate 36-row/18-table witness
  retains selected data and inspected catalog definitions through marker
  roundtrips; queued intents remain attempts zero.
- Native comparison, deletion, drift refusal, generated-contract, tenant,
  security/deployment and static checks have separate source-bound receipts.
  Collection is not full-backend execution; overlapping cohorts are not summed.

The latest parity module passes 15 cases at each appropriate head, including four
actual scoped deletion cases and one portable fixture guard. Its source extends
the earlier 11-case component proof; both remain dated separately. All 5,203
backend cases are collected, with no local full-backend execution claim.

The full combined backend, ordinary visual assertions, image/storage audits and
all completion contexts need fresh hosted qualification at the final proposed
main combination. Individual green feature heads do not qualify this union.
The initial gate source's real cancellation refusal is one bounded native
observation, not a guarantee against all scheduler/runner failures.
No calendar-provider, SMTP, bank, worker-delivery, accountant or production
acceptance is established by mocked browsers or directly seeded migration data.

## Logged follow-up and maintenance prompts

**UI-PLANNING-FOCUS-01:** reverse-Tab from a scrolled calendar can focus New
assignment behind the sticky header. It reproduces on both original planning
and the integration; the initial buttons are visible and manual scrolling
restores them. Preserve the read-only paired DOM/keyboard evidence in
`frontend/header-probe/`. Prompt: add a native reverse-Tab regression, fix the
smallest scroll/focus offset boundary, and verify visible unobscured focus at
narrow/enlarged sizes without changing shared navigation or payment behavior.
The current screenshot ordering alone is a capture artifact, not evidence of
missing controls.

**Migration maintenance:** preserve every historical revision and exact branch
parent. Test actual upgrades and the one-head graph from each retained source
state, both markers' safe reversal/reapplication and interrupted-index refusals.
Retain rows/catalog/RLS facts, strict actual comparison and relevant negative
controls. Never equate an index downgrade or removal of calendar schema with a
safe whole-application rollback. Compatibility rollback must retain payment
receipt APPROVE checks in both legacy expense lookup aliases.

**Integration maintenance:** preserve the full tenant/worker/PG/browser registry
union, current security fixes and result-only completion jobs. Requalify source,
base, generated contracts and fresh complete CI after further composition.
Do not fix stale dependency PRs by duplicating superseded pins or weakening
security/visual assertions. Keep branch enforcement, F10 history classification,
served-SHA/readiness, full EX19/EX21–22/EX17 and accountant acceptance open.

## Review and recovery

Before any later production integration, obtain human understanding of the
inherited UI/financial/provider scope, migration graph and comparison boundaries.
Writer quiescence, backups, deployed revision and provider configuration require
separate release evidence. Code rollback preserves financial receipts/audit/outbox
and all source branches; graph-only reversal is not permission to remove data.

A temporary local test credential appeared in an early diagnostic. It was rotated,
private files refreshed, retained diagnostics redacted and fixture representation
regressions added before qualification resumed. No production credential or
Hostinger access was involved. Earlier harness/type failures are retained; no
warning, schema, security or test gate was waived.

AI assistance covers reconciliation, implementation, adversarial review, synthetic
fixtures, CI evidence and documentation. It is not human approval. No unreviewed
main merge, production migration or deployment is performed.
