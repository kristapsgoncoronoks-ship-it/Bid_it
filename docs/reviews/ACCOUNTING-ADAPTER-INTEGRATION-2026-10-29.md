# Accounting adapter integration — 29 October report follow-up

**Reviewable candidate; delivered-head CI and human/Lead acceptance remain
separate.** This composes reviewed PR #96
`82d0a02d912b8ed66a6693a665d155d8821079ce` with qualified report/development
PR #97 `cf25f75680115438578f1d1f4663f35c677a79ac`. Main remains
`90891d8d52cf5aefc4467e3fa7f6070c042acec2`; no main merge or deployment occurs.

## Result and source boundaries

Xero and QuickBooks remain unavailable. The shared service catalogue supplies
their labels, action IDs, reasons and setup steps; public and internal render
paths refuse unconfigured formats before iterating invoice rows. The unreviewed
legacy branded renderers and guessed account/tax defaults are removed. The only
production renderer caller remains the existing generic accounting preview.

The capability query belongs to the current session/user/org/permission cache
identity, is abortable and bounded at 15 seconds, and has no polling or automatic
retry. Manual retry reads metadata only. Native disabled buttons have unique
accessible descriptions; keyboard setup disclosures perform no export command.
Unknown, missing, duplicate, malformed, mismatched-action or unsupported enabled
provider descriptors fail closed while the generic preview remains independent.

The four backend feature files match reviewed PR #96 exactly. Independent source
and AST comparisons preserve generic ledger/CSV/money functions, all router
permission dependencies, current owned QueryClient/session fencing and 274
protected files, including all 155 migrations, models, workflows, locks, Docker
sources and comparison/development-bootstrap code. There is one existing
`f0b2d4e6a8c1` migration head; this change adds no model, migration or dependency.
Existing security repairs are inherited rather than replaced by older main locks.

## Composition and API contract

The actual local composition conflicts were limited to the component and its
R002 browser spec. Both sides' behavior is retained: current `min-w-0/max-w-full`
and wrapping, correctly typed organization responses, all original generic
preview cases and the reviewed capability/error/setup cases.

OpenAPI was regenerated using the repository's `python -m app.openapi` command.
All 417 paths remain; all 14 newer paths absent from PR96's older snapshot are
unchanged. Only the existing `/api/v1/export/formats` response changes, with one
new `AccountingFormat` schema; no existing component schema is removed/changed.
The automatic JSON merge already matched the actual regeneration. This exposes
the additive contract for human review; it is not baseline self-approval.

## Reproduced error-surface defect and repair

**ACC-COMPOSE-ERROR-01:** a synthetic catalogue 500 response displayed its raw
`PRIVATE_CONFIGURATION` detail in the global toast even though the component
rendered a safe inline error. A rejected catalogue also exposed the internal
parser message. Two actual browser controls failed immediately with one
forbidden toast, without waiting for the toast's five-second expiry.

The catalogue query now uses existing `meta: { errorHandled: true }` behavior.
Its inline failure/retry remains visible, and the global query handler is
unchanged. Immediate regression checks cover both raw-detail and parser cases.
No actual customer/configuration data exposure is asserted by synthetic probes.
Stable `generic/ACC01`, `xero/ACC02` and `quickbooks/ACC03` checks additionally
close the source-reviewed descriptor consistency gap; two cases fail against
the earlier parser. That gap was not established as a provider or tenant breach.

## Browser integration and historical evidence

The first full run found an existing enlarged-layout test returning `[]` for the
new catalogue endpoint while expecting the two disabled provider buttons. The
production service returns three typed descriptors; the fixture now supplies
that contract. Every original layout/disabled-state assertion remains.

That run was stopped with exit 130: 819 completed expected cases, one fixture
failure, four interrupted in-flight cases and 118 unstarted cases. It is not a full-suite pass or a
test waiver. The affected cohort passes after the fixture correction, followed
by a fresh frozen-source full run recorded separately.

Four Schedule screenshots previously targeted an older review folder. They now
use Playwright `testInfo.outputPath`; only two callback parameters and four paths
change. All assertions are byte-preserved. One historical PNG overwritten by the
initial run was retained as attempt evidence, then restored. All four historical
files match both pre-run backups and their retained original SHA256SUMS, including
restored mtimes. New output is isolated per test and no visual golden is changed.

Impeccable's narrow Operate/harden review preserved incumbent design. Final
desktop, 390px mobile and enlarged-text captures show readable secondary copy,
visible keyboard focus and no horizontal overflow. Actual 14px/28px text and
contrast observations are recorded. Initial 12px computed metrics are retained,
but the earlier PNGs were refreshed before preservation; there is no valid
paired before/after image comparison. Expanded setup help can still grow the
action header; UI-ACC-HEADER-01 is a nonblocking layout refinement. No broad UI
redesign, physical-device coverage or accessibility certification is claimed.

## Executed checks and limits

| Cohort | Actual result / scope |
| --- | --- |
| ERP/R002/architecture/authz/route coverage | 76 passed, zero skipped; 80 existing synthetic test-key warnings; 45.65s |
| Prior/current renderer control | Old Xero/QuickBooks empty headers reproduced; new refusal before row iteration; generic CSV byte-identical/formula-safe at EUR121.00 |
| Generated API/ERD/Playwright pairing | 34 passed in 4.03s |
| Focused R002 browser | 17 passed; includes the four new ID/error-surface cases |
| Affected R002/invoice-context/Schedule browser | 42 passed in 21.66s, no skips/flakes; overlaps the 17 cases |
| Full default browser | 942 passed in 365.21s; 942 unique IDs, one passed attempt each, no retries/skips/flakes/global runner errors; process exit zero |
| Frontend lint/build/proxy/bundle | Passed; 19 proxy cases; first-load 437.8/129.0kB within unchanged 460/135 limits |
| Whole-app source style/typing | Ruff/check-format, 889 files; mypy 436 files pass with existing notes |
| Backend collection | 5,248 cases; no new full local backend run claimed |

The browser provider/error cases use controlled API fixtures. They prove blocked
states, safe recovery, layout and no export command from setup, not provider
import, bank acceptance or a complete action-specific real-stack identity matrix.
Existing generic-preview API tests use synthetic SQLite fixtures. No new native
PostgreSQL concurrency/financial behavior is introduced. Fresh delivered-head
CI, including native/visual/image gates, still applies; preceding PR #97 success
does not qualify this new code. No performance improvement or hardware capacity
is inferred from an unchanged first-load metric.

## Maintenance prompt and remaining queue

> Preserve the shared public/internal unconfigured-format refusal before row
> iteration, all existing generic signed-preview/Decimal/formula/tenant rules
> and current source security repairs. Keep provider metadata scoped/abortable,
> fail closed on malformed or mismatched descriptors, and use handled query
> errors so sanitized inline states never leak raw details through global toasts.
> Preserve visible reasons, unique descriptions and native keyboard setup with
> zero export commands. Use typed fixtures and per-test output paths; retain
> failed attempts and historical artifacts. Revalidate all current API paths
> and expose the semantic schema delta. Do not enable an adapter without its
> separately reviewed provider/product/region/template and acceptance contract.

Reimbursement artifact recovery RB04 → RB05/EX22 → EX17 → EX21 remains next,
with Decisions §33, full EX19, B00/F10/readiness and accountant acceptance open.
The unavailable-adapter implementation does not choose mappings, tax defaults,
retention, bank profiles or a production storage/provider contract.

## Rollback and review

Revert UI/integration changes independently if necessary, while retaining public
and internal refusal of unreviewed branded exports. No schema/data rollback is
needed. Do not restore guessed renderers, older dependency/workflow/image pins or
weaker tests as a rollback. Production is separately managed; its served revision
was not verified here. This candidate does not alter the user's VPS.

[Machine receipt](evidence/ACCOUNTING-ADAPTER-INTEGRATION-2026-10-29.json) binds
source, semantic diffs, executed commands, controls, attempts and cleanup. Detailed
sanitized artifacts live in
`/workspace/artifacts/accounting-adapter-integration-2026-10-29/`.
AI-assisted: reviewed composition, implementation, fixtures, independent source/
design review and evidence drafting. Human/Lead and generated-contract review
remain pending. No unreviewed main merge, production migration, provider action,
repository-setting change or deployment occurred.
