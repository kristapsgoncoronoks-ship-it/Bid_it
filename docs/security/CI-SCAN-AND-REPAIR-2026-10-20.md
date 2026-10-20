# CI scan and repair — 20 October 2026

**Status: repairs implemented and locally qualified; hosted CI and Lead review
remain separate.** Current development
checkout: `86dc1146f9c7e5fed2148ed9d76eeb464df7fcfc`, with earlier local UI and
workflow changes preserved. This report separates hosted results, local repairs
and the isolated integration candidate. None constitutes production approval.

## Hosted findings and ordered repair plan

The read-only GitHub scan inspected **1,087 available runs across 11 pages**,
nine branches, four open PRs and six registered workflows. Historical failures,
cancellations and startup failures are not counted as current application bugs.
The original scan and exact image-artifact hashes are retained in the
[sanitized evidence](evidence/CI-SCAN-AND-REPAIR-2026-10-20.json).

| Priority / finding | Evidence and action | Status |
| --- | --- | --- |
| P1 — CI-20-IMAGE-INTEGRATION | PR #65 branched before 32 development commits. Run `39370619440` has 71 backend advisory/package rows (70 HIGH, one CRITICAL) and two frontend HIGH rows; its old gate covers only two runtime images. Integrate the accepted development implementation, retain all build/service targets, and qualify the combined expense/capture behavior. | Isolated candidate prepared against PR #65 snapshot `5796feb`; native functional checks pass. New-head ten-image scan remains required; no direct update of the active expense branch. |
| P1 — CI-20-MIGRATION-HEADS | Integration exposes two independent Alembic heads: capture admission `238cd0be715f` and expense receipts `b018e9f4c021`. Add a no-op merge revision with both parents; do not rewrite either published migration. | Merge revision `c129fa806b32`; single-head fresh SQLite upgrade/drift and PostgreSQL upgrade pass. All 174 registered PostgreSQL checks pass. |
| P1 — V07 historical identifier classification | Latest nightly run `39360057871` reports 872 structural matches across 7,846 blobs; owner-held hashed denylist is empty. Keep enforcement, privately classify each match and obtain the named verifier/owner process. | Owner-dependent; no identifier publication, blanket allowlisting or history rewrite. [Decision §24](../DECISIONS-NEEDED.md#24-the-nightly-pii-history-scan-is-red--who-verifies-the-historic-hits-sec-013-high). |
| P2 — CI-20-PG-COVERAGE | Removing a PostgreSQL test from the command and placing its path in a comment still passed the old QA-001 guard. Discover actual environment reads and require an executable, unfiltered pytest command in the PostgreSQL job with the right URL and working directory. | Repair and failure-first regressions implemented; independent review also caught and repaired inherited `PYTEST_ADDOPTS` suppression. |
| P2 — CI-20-AUDIT-EVIDENCE | A failed Python audit skipped the independent npm audit and evidence upload. Continue independent checks after failures, retain their nonzero outcomes and upload available JSON plus explicit blocked/missing-report status. | Repair implemented; 30 focused governance checks pass, including 12 new command-flow cases. |
| P2 — CI-20-ACTION-COMMENTS | Combined governance tests found two expense-workflow action pins without their version comments, failing the existing Dependabot-maintenance guard. Restore the comments matching the already used SHAs. | Reproduced one failure / 161 passes, then repaired in the integration candidate; no pin or guard changed. |
| P2 — CI-20-CUSTOMER-ERROR-MOCK | The root working tree's new customer directory requests no longer matched the FE-004 fault interceptor, so its existing error-state assertion failed. Target `/customers/directory` while retaining every assertion. | Root-only fixture repair: all 22 affected-file cases pass. This earlier UI/directory work is not part of the integration branch. |
| P2 — CI-HOST-TSC | Host frontend CI still executes the npm compiler with embedded Go 1.26.4, although the Docker build uses the exact-source security rebuild. Build/extract the same pinned stage, validate identity/checksum/notices and replace only the npm-selected native executable. | Native first/repeat installation, compiler diagnostics, unchanged library/assets and 23 regressions pass. Host CI invocation is included in the candidate; exact-head CI pending. |
| P2 — CI-20-PG-SCHEMA-DIFF | An additional PostgreSQL `alembic check` reports four existing column-specific `SET NULL` differences for project/issuer composite foreign keys. Their models are unchanged from development; the fresh migration itself passes. Qualify a dialect-aware comparison without replacing the constraints with broader nulling behavior. | Baseline comparison retained; not a new expense-table drift or a current hosted migration-gate failure. |
| P2 — superseded dependency branches | PRs #61/#63 retain failures at their old heads. Their accepted dependency updates and compatibility repairs are in development, not thereby in those branches or main. | Preserve old evidence; review supersession after integration CI rather than merging stale pins. |

The image rows are package/advisory findings, not 73 distinct product defects.
The CRITICAL row is Debian `libxml2`, `CVE-2026-6653`, without a listed fixed
version in that artifact. Frontend rows are `libexpat` 2.8.5-r0 → 2.9.0-r0
(`CVE-2026-77214`) and `libx11` 1.8.13-r0 → 1.8.14-r0 (`CVE-2026-88806`). The
development image implementation replaces the affected runtime/build paths;
new-head scanning must establish its result for the combined candidate.

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
| Candidate frontend | Frozen install, lint/typecheck, production build and 19 proxy tests pass; 434.6 kB raw / 128.3 kB gzip below unchanged 460/135 limits |
| Host compiler | 23 regression cases (included in combined guard count); real install/repeat pass, six Unicode fixtures and diagnostics match; 86 JS assets byte-identical and 416 npm launcher/library files unchanged |
| Fresh ecosystem audits | 78 Python packages, none skipped, zero advisory rows; 188 npm dependency entries, zero advisories at every severity |
| Workflow validation | actionlint 1.7.12 clean; zizmor 1.30.0 offline has zero findings; pins and control register pass |
| Candidate types and style | Mypy passes all 423 source files (two existing notes); Ruff lint/format pass 849 files |
| Root working tree | Broader backend/browser checks run separately against earlier uncommitted UI/workflow changes; their final status belongs to the evidence, not the isolated candidate |

Counts above overlap where stated and must not be added into a synthetic total.
The original development head passed hosted CI
`39268926725` and supply-chain `39268926651`; that result does not qualify the
new working-tree repairs or the combined candidate.

All local checks use Python 3.12 / Node 24 unless stated otherwise; hosted Python
3.11 / Node 22 and shipped Python 3.14 remain distinct targets. No candidate
full-suite, fresh ten-image result, new-commit CI or Lead approval is implied by
a focused pass. Production provider/cutover approval remains separately required.

Rollback the CI guard/diagnostic/compiler changes together with their tests and
workflow invocation, retaining audit enforcement. Reverting the integration
returns to the known red image baseline; it is not a safe release alternative.
The merge migration is schema-neutral; it does not discard command evidence or
alter existing money, tenant, retention or access policy.

AI-assisted: hosted inventory, code/test drafting, independent negative probes,
integration and documentation. No external implementation copied. Lead review
and CI on the delivered commit remain required.
