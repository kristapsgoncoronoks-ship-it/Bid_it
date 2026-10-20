# CI scan and repair — 20 October 2026

**Subsequent rollout repair:** [VPS writer quiescence](CI-VPS-WRITER-QUIESCE-2026-10-20.md)
now enforces the capture migrations' explicit preflight. Twenty executable Bash
regressions and native Compose stop/refusal/resume checks pass. New-head hosted
qualification is pending; the green `22c9b3af` checks below retain their earlier
source scope. Hostinger uses existing local storage; the bundled S3 provider
choice is separate. The user's instruction to proceed reaffirms normal merge
authorization without assuming an unreadable deployment variable is false.

**Latest main integration:** [PR #67 and fresh main-target evidence](CI-MAIN-INTEGRATION-2026-10-20.md)
now pass all seven required CI jobs and all four supply-chain jobs on `22c9b3af`.
The reported red run was a manual rerun of unchanged main `7608c1e`; it retained
the old two-image findings. Main is not merged. Whether a merge deploys the live
app remains a pending factual clarification because GitHub denies access to
`DEPLOY_ENABLED`. Earlier expense-target qualification below is preserved.

**Status: final head `22c9b3af` passed all seven required CI jobs and all four
supply-chain jobs. Lead review remains pending.** Current development
checkout: `86dc1146f9c7e5fed2148ed9d76eeb464df7fcfc`, with earlier local UI and
workflow changes preserved. This report separates hosted results, local repairs
and the isolated integration candidate. None constitutes production approval.

**Published for review:** [draft PR #66](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/pull/66),
latest head `22c9b3afbfbeb81296a7c06490da9c855aa2d730`, targets the expense branch.
Its [CI](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39377525875)
and [supply-chain](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39377525884)
checks passed on their first attempt; GitHub reports mergeable with clean status.
Prior repair head `2701ad1550cebbabf8977d896e362d317e8f0e22`'s
[CI](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39374113766)
and [supply-chain checks](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39374113767)
have passed all seven required CI jobs and all four supply-chain jobs. The hosted
image gate is green for all ten targets; all six native storage groups pass.
The active expense branch was not changed directly. Updates in the
original checkout's report after publication do not silently update the PR.

Final mergeability verification found that the actual expense branch had moved
to `09ec55f6e5c0e20cf6680419b0092d202f5fa762`, while REST's PR base object and
the synthetic merge ref still described the earlier base. The new target commit
adds an early supply-chain test and changes README counts. A README-only conflict
is resolved by retaining the integration's verified 4,517 count; the target's
early test remains. New head `22c9b3af` changes only the CI invocation and trailing
whitespace relative to the passing head. All 99 affected CI/docs checks,
actionlint and offline zizmor pass; new-head hosted qualification also passed.
Live branch refs, not only the cached PR base object, are required for closure.

## Hosted findings and ordered repair plan

The read-only GitHub scan inspected **1,087 available runs across 11 pages**,
nine branches, four open PRs and six registered workflows. Historical failures,
cancellations and startup failures are not counted as current application bugs.
The original scan and exact image-artifact hashes are retained in the
[sanitized evidence](evidence/CI-SCAN-AND-REPAIR-2026-10-20.json).
Release, Dependabot Updates and Dependency Graph also have successful latest
runs at main's recorded head; their success is not release or dependency-policy
approval. Main's image and historical-PII failures, and the old dependency-PR
failures, retain separate head-specific dispositions below.

| Priority / finding | Evidence and action | Status |
| --- | --- | --- |
| P1 — CI-20-IMAGE-INTEGRATION | PR #65 branched before 32 development commits. Run `39370619440` has 71 backend advisory/package rows (70 HIGH, one CRITICAL) and two frontend HIGH rows; its old gate covers only two runtime images. Integrate the existing development implementation, retain all build/service targets, and qualify the combined expense/capture behavior. | Draft PR #66's ten-image hosted gate passes with zero HIGH/CRITICAL rows. All 20 retained report hashes/image identities and six storage groups verified; no direct update of the active expense branch. |
| P1 — CI-20-MIGRATION-HEADS | Integration exposes two independent Alembic heads: capture admission `238cd0be715f` and expense receipts `b018e9f4c021`. Add a no-op merge revision with both parents; do not rewrite either published migration. | Merge revision `c129fa806b32`; single-head fresh SQLite upgrade/drift and PostgreSQL upgrade pass. All 174 registered PostgreSQL checks pass. |
| P1 — V07 historical identifier classification | Latest nightly run `39360057871` reports 872 structural matches across 7,846 blobs; owner-held hashed denylist is empty. Keep enforcement, privately classify each match and obtain the named verifier/owner process. | Owner-dependent; no identifier publication, blanket allowlisting or history rewrite. [Decision §24](../DECISIONS-NEEDED.md#24-the-nightly-pii-history-scan-is-red--who-verifies-the-historic-hits-sec-013-high). |
| P2 — CI-20-PG-COVERAGE | Removing a PostgreSQL test from the command and placing its path in a comment still passed the old QA-001 guard. Discover actual environment reads and require an executable, unfiltered pytest command in the PostgreSQL job with the right URL and working directory. | Repair and failure-first regressions implemented; independent review also caught and repaired inherited `PYTEST_ADDOPTS` suppression. |
| P2 — CI-20-AUDIT-EVIDENCE | A failed Python audit skipped the independent npm audit and evidence upload. Continue independent checks after failures, retain their nonzero outcomes and upload available JSON plus explicit blocked/missing-report status. | Repair implemented; 30 focused governance checks pass, including 12 new command-flow cases. |
| P2 — CI-20-ACTION-COMMENTS | Combined governance tests found two expense-workflow action pins without their version comments, failing the existing Dependabot-maintenance guard. Restore the comments matching the already used SHAs. | Reproduced one failure / 161 passes, then repaired in the integration candidate; no pin or guard changed. |
| P2 — CI-20-MOVING-BASE | PR #66's REST base object and synthetic merge ref retained `5796feb` after the live expense branch advanced to `09ec55f`. Check live Git refs and graph ancestry before closure; reconcile the README conflict and retain the target's early regression step. | Merge head `22c9b3af` includes the actual target; 99 affected checks and all required hosted checks pass. Native Git refs agree and GitHub reports mergeable with clean status. |
| P2 — CI-20-CUSTOMER-ERROR-MOCK | The root working tree's new customer directory requests no longer matched the FE-004 fault interceptor, so its existing error-state assertion failed. Target `/customers/directory` while retaining every assertion. | Root-only fixture repair: all 22 affected-file cases pass. This earlier UI/directory work is not part of the integration branch. |
| P2 — CI-HOST-TSC | Host frontend CI still executes the npm compiler with embedded Go 1.26.4, although the Docker build uses the exact-source security rebuild. Build/extract the same pinned stage, validate identity/checksum/notices and replace only the npm-selected native executable. | Native first/repeat installation, compiler diagnostics, unchanged library/assets and 23 regressions pass. Hosted frontend CI consumes the verified Go 1.27.2 rebuild; its executable SHA-256 matches the local native qualification. |
| P2 — CI-20-PG-SCHEMA-DIFF | An additional PostgreSQL `alembic check` reports four existing column-specific `SET NULL` differences for project/issuer composite foreign keys. Their models are unchanged from development; the fresh migration itself passes. Qualify dialect-specific expected metadata without broadening the constraints. | [Concrete plan and development prompt](../plan/CI-PG-SCHEMA-COMPARISON-2026-10-20.md): native positive/negative comparison and tenant-preserving delete checks, including non-production create_all. Baseline comparison retained; not a current required hosted-gate failure. |
| P2 — superseded dependency branches | PRs #61/#63 retain failures at their old heads. Their accepted dependency updates and compatibility repairs are in development, not thereby in those branches or main. | Preserve old evidence; review supersession after integration CI rather than merging stale pins. |
| P3 — CI-20-FEEDBACK | Prior full backend took 1,801.86 seconds; the single-worker browser suite took 14.8 minutes. Per-test/fixture and CPU/RSS evidence is missing, so fixture and parallelism benefits remain unmeasured. | [Ordered measurement and optimization plan](../plan/CI-FEEDBACK-PERFORMANCE-2026-10-20.md). Retain early guards and all existing test, security and resource controls. |

The image rows are package/advisory findings, not 73 distinct product defects.
The CRITICAL row is Debian `libxml2`, `CVE-2026-6653`, without a listed fixed
version in that artifact. Frontend rows are `libexpat` 2.8.5-r0 → 2.9.0-r0
(`CVE-2026-77214`) and `libx11` 1.8.13-r0 → 1.8.14-r0 (`CVE-2026-88806`). The
development image implementation replaces the affected runtime/build paths.
The combined candidate's hosted scan now reports zero HIGH/CRITICAL rows across
all ten image targets; this does not alter the old run's failed result.

## Repair boundaries

QA-001 now parses the PostgreSQL job rather than grepping the whole workflow.
Comments, another job, SQLite/missing URLs, filtered/collect-only selections,
disabled steps and shell expressions do not count as coverage. The supported
contract is deliberately limited to direct pytest invocations; arbitrary shell,
plugins and dynamically computed environment reads need explicit extensions.

Dependency review keeps its audit thresholds, pinned actions and read-only
permissions. Failed and unavailable reports remain blocked. Tests execute the
committed Bash with stub audit/provider failures and a bounded Actions-condition
evaluator; they do not claim to emulate all of GitHub Actions.

The compiler installer reuses the existing Docker stage and qualified source;
it does not introduce another compiler fork or change the dependency lock. It
checks source/archive identity, Go and Unicode-module versions, binary SHA-256,
reported compiler version and linked license notices before installation.
Provenance symlinks are refused, native replacement is atomic and extraction
cleanup preserves primary failures. Real native validation also caught and
repaired two problems missed by early stubs: the noembed executable needs the
adjacent npm libraries even for its version probe, and repeated copying over
read-only Go license notices fails. The candidate is now probed in the npm
library directory before replacement; byte-identical retained provenance is
reused and differing provenance refuses with clean-install guidance. Build
provenance is not an independent signature or a fresh vulnerability audit.

The isolated integration preserves the expense commands and the bounded upload
reader. It regenerates OpenAPI and the ERD instead of choosing one conflicting
snapshot. Relative to committed development, OpenAPI adds two expense command
paths and modifies three expense paths; no paths or schemas are removed.
Earlier uncommitted UI/password/directory work is not silently included in that
candidate. Its review and qualification are separate from this working tree.

The combined PostgreSQL test first reproduced two concurrency-test failures:
three simultaneous multipart imports meet the restored two-upload admission
limit, so one correctly returns `503 / upload_busy`. The regression now retries
only that stable code with the identical command after the first batch settles.
It retains all three eventual-success, financial, command-identity and audit
assertions, and checks durable counts before and after recovery. Unrelated 503s
and 500s are not retried; no production admission limit is widened.

## Validation and remaining acceptance

| Target | Local evidence |
| --- | --- |
| Combined CI guard/security suites | 162 passed; failure-first run reproduced two missing action-version comments as one failing guard |
| Combined expense/upload/docs focus | 68 passed |
| Real PostgreSQL, registered gate | 174 passed across 39 modules; non-superuser/non-BYPASSRLS, 115 tables, new merge head; owned container/storage/credentials removed |
| Combined real expense-browser workflow | Six passed on the first run, no skips/flaky cases; existing response-loss/read-failure injections retained alongside real HTTP/PostgreSQL import, replay, removal/restore and audit readback. Existing API/auth limits and two-upload capacity stayed active; owned API, Vite, database and credentials removed. |
| Candidate frontend | Frozen install, lint/typecheck, production build and 19 proxy tests pass; 434.6 kB raw / 128.3 kB gzip below unchanged 460/135 limits |
| Host compiler | 23 regression cases (included in combined guard count); real install/repeat pass, six Unicode fixtures and diagnostics match; 86 JS assets byte-identical and 416 npm launcher/library files unchanged |
| Fresh ecosystem audits | 78 Python packages, none skipped, zero advisory rows; 188 npm dependency entries, zero advisories at every severity |
| Workflow validation | actionlint 1.7.12 clean; zizmor 1.30.0 offline has zero findings; pins and control register pass |
| Candidate types and style | Mypy passes all 423 source files (two existing notes); Ruff lint/format pass 849 files |
| Hosted full backend | Final head: 4,361 passed / 156 skipped / 3,472 warnings in 2,308.93 seconds (38m29s). Earlier focused cases overlap the full run and are not added. Prior repair head had the same outcomes in 1,801.86 seconds; different runs do not establish a performance regression or improvement without runner/resource attribution. |
| Hosted exact-head gates completed | All seven required CI jobs and all four supply-chain jobs pass. This includes full backend, lint, tree PII, frontend, 174 PostgreSQL checks, performance gate, six real expense-browser cases, 620 functional browsers, 13 visual checks, ten-image audit and six native storage groups. Optional baseline refresh and deployment are skipped as intended. |
| Root working tree | Full backend: 4,356 passed / 164 skipped / 3,606 warnings in 40m01s. It collected 4,520 cases before new CI cases were written; the final 82-case CI/docs run covers those additions separately. Browser sweep: 671 passed / one stale customer mock failed, then 22 affected-file passes. All 672 unique browser cases are verified across phases, not a fresh single full rerun. Earlier UI/workflow changes remain separate from the candidate. |

Counts above overlap where stated and must not be added into a synthetic total.
Both hosted workflows completed successfully on their first attempt. At final
verification, PR #66's head remains `22c9b3afbfbeb81296a7c06490da9c855aa2d730`
and the live expense-branch base remains `09ec55f6e5c0e20cf6680419b0092d202f5fa762`.
Both are checked with native Git refs; the actual base is an ancestor of the head.
The PR is open and draft; no merge or deployment occurred.
The original development head passed hosted CI
`39268926725` and supply-chain `39268926651`; that result does not qualify the
new working-tree repairs or the combined candidate.

All local checks use Python 3.12 / Node 24 unless stated otherwise; hosted Python
3.11 / Node 22 and shipped Python 3.14 remain distinct targets. Hosted completed
jobs now qualify their actual runtimes; storage reports Python 3.14.8 / UID
10001. The full candidate backend job passed; Lead approval remains open. Production
provider/cutover approval remains separately required.

Final-head artifact `12244054007` has a matching GitHub archive digest. All 20 retained
sanitized SBOM/audit hashes were independently recomputed; ten immutable image
IDs, the pinned external RustFS digest and the exact-head inventory agree.
Pre-sanitization hashes are recorded by the producer, but raw reports are not
retained, so those additional hashes are not claimed independently verified.
Prior-head artifact `12243327445` and its qualification remain separately scoped
in the evidence; neither replaces the final-head verification.

The local expense profile recorded 95 API requests (one expected 404), about
148.2 MiB API peak RSS and 7.01 user-CPU seconds over 309.59 elapsed seconds.
Existing 61-second fixture pacing dominates elapsed time. The API ran on the
shared host; these figures exclude browser/database memory and do not establish
production throughput or minimum hardware. Local psql used a temporary transport
adapter to the same real PostgreSQL client/server; hosted CI uses its native psql.

Rollback the CI guard/diagnostic/compiler changes together with their tests and
workflow invocation, retaining audit enforcement. Reverting the integration
returns to the known red image baseline; it is not a safe release alternative.
The merge migration is schema-neutral; it does not discard command evidence or
alter existing money, tenant, retention or access policy.

AI-assisted: hosted inventory, code/test drafting, independent negative probes,
integration and documentation. No external implementation copied. CI passed on
the delivered commit; Lead review remains required.

Independent closure review found no further material CI bypass or source-scope
error. The twelve final candidate source hashes match the published head. This
review does not substitute for Lead acceptance.
