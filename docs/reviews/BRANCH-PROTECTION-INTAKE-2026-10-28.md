# Branch-protection policy intake — 28 October 2026

The uploaded configuration ZIP and separately uploaded policy PDF are review
evidence and proposed settings. They do not authorize configuration changes,
feature integration, human approvals, bypasses or production deployment. This
change preserves the proposed JSON, adds bounded backlog tasks and records fresh
read-only observations. `OPS-GOV-001` / B00 remains open.

## Inputs and provenance

| Input | Bytes | SHA-256 |
| --- | --- | --- |
| Uploaded configuration ZIP | 87,959 | `a2f88ec14bfc8713cb2b3bcb20d0a4ee693eec48a5f5b5c938142ddcc2085e7e` |
| Separately uploaded policy PDF | 121,176 | `8029a6c9b5ddafe0bfb8bbfe903fe40dc105c08330c4786504c0a85685390840` |
| Policy PDF inside ZIP | 96,204 | `19cbc82c7f8d73851da26a785fdfe5459287d3299eb0cb395ddd179687d74506` |

The PDF files have different byte identities but identical `pdftotext -layout`
output across all nine pages. No precedence decision is needed for their text.
The ZIP has six regular files, no executable scripts or unsafe extraction
paths. All four JSON inputs parse locally. The source evidence snapshot was
captured at `2026-10-28T04:59:58Z`; subsequent live observations below are separate.

Byte-preserved [configuration proposals](../security/branch-protection-proposals/2026-10-28/README.md)
are reviewable in the repository. The full inputs, parsed PDF text, independent
review and sanitized GitHub responses remain under
`/workspace/artifacts/branch-protection-review-2026-10-28/`.
[Machine receipt](evidence/BRANCH-PROTECTION-INTAKE-2026-10-28.json) binds the
input hashes, source revisions and observations.

## Fresh observations and corrections

Main remains `90891d8d52cf5aefc4467e3fa7f6070c042acec2`.
The branch endpoint reports `protected=false`, check enforcement `off` and no
required contexts. Both the repository ruleset list and effective main-rules
endpoint succeed with empty arrays. The detailed classic-protection endpoint
returns **403, Resource not accessible by integration**. The proposal's earlier
connector rejection for effective rules is superseded by this successful fresh
read; the separate detailed-protection access limit remains. Repository metadata
does not prove Administration API capability or justify broader credentials.

PR #87 remains an open draft at
`5926b847d4dce686f88b07123e5df8ddb9bcceb1`. Core
[40538185021](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/40538185021)
and supply-chain
[40538185092](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/40538185092)
are completed successfully, with all eleven proposed required job contexts
successful. `deploy` and `vr-baselines` are skipped as designed. This updates
the preceding candidate's pending-workflow observation; it is not a new
independent test-count, image-digest or whole-release qualification claim.

The eleven exact contexts are `pii-scan`, `lint`, `backend`, `postgres`,
`frontend`, `frontend-e2e`, `docker-build`, `Workflow security`,
`Generic credential scan`, `Dependency review`, `Security control register`.
All are observed from GitHub Actions app `15368`. Proposed rulesets use
`integration_id`; the classic alternative uses `app_id`. Do not require workflow
titles, `deploy`, `vr-baselines` or scheduled/manual `pii-history` as PR checks.
Current workflows lack `merge_group`; merge queue needs separate implementation.

## Ordered, separately reviewable actions

These extend B00 and Decisions section 23; they do not silently replace its
earlier seven-check/solo/admin-hotfix proposal. The recommended eleven-check,
empty-bypass, independent-review model needs explicit owner selection.

| ID / priority | Outcome and state |
| --- | --- |
| B00-GH-01 / P1 | Select reviewer/solo mode and approve a main-only ruleset/recovery policy. **Owner decision and admin access required; not applied.** |
| B00-INT-01 / P1 | Refresh the planning-plus-finance integration and reconcile its migration graph before integration. **Engineering task; existing #80 is not current combined qualification.** |
| B00-CI-01 / P1 | Add fail-closed same-workflow completion jobs, with skipped/failed-child controls. **Planned code work; do not require nonexistent names.** |
| B00-CI-03 / P1 | Inspect failed #81/#88–90 at their recorded source heads and repair causes individually. **Failure inventory only; root-cause analysis still needed.** |
| B00-GH-02 / P1 | Verify repository Actions permissions and production environment controls; preserve least privilege. **Read visibility/owner settings decision still needed.** |
| B00-GH-03 / P1 | Name eligible code owners before creating enforceable ownership requirements. **Owner assignment needed.** |
| B00-GH-04 / P1 | Rehearse activation, normal path and narrow recovery; archive effective-rule and refused-operation proof. **After authorized policy selection.** |
| B00-CI-02 / P2 | Enforce accepted cross-PR dependencies if native main rules and the review ledger are insufficient. **Separate later gate, not a new executor.** |

### B00-GH-01 — apply only the selected protection model

**Prompt:** Refresh/export the current main settings and eleven exact successful
PR check contexts/app IDs. Obtain the owner's selected independent-review or
explicit lower-assurance solo model, named eligible reviewer, merge-method and
recovery decisions. Use the supplied two-rule proposal or a justified alternative
without weakening stronger controls. Keep the durable baseline outside any
review-only exception. Preserve current controls when access or rehearsal fails;
do not treat AI review as approval. Apply only within explicit authorization and
capture IDs, effective readback, accepted/rejected paths and recovery evidence.

### B00-INT-01 — current combined migration graph

PR #80's merge `e5f7091a2345` joins `c3d5e7f90112` (planning) and
`d4e6f8a01223` (region OCR). PR #87's new payment-index migration
`a9c3e7f1b5d0` also follows `d4e6f8a01223`. The unmodified union therefore has
two heads: `e5f7091a2345` and `a9c3e7f1b5d0`. This is a migration-graph finding,
not a newly reproduced runtime upgrade failure; no combined database was upgraded
in this intake. #87 does not include #76's planning work.

**Prompt:** From refreshed main, prepare an isolated reviewable candidate combining
current #76 and #87, carrying forward justified #80 conflict resolutions. Preserve
existing migration histories; add a reviewed reconciliation revision if required.
Validate actual heads and upgrades from main, both feature heads and the older
integration head on empty and representative PostgreSQL/SQLite databases. Check
RLS, index validity, preserved financial/audit/receipt/outbox rows, downgrade limits
and the known `DB-PG-PARITY-01` comparison issue separately. Run all eleven jobs on
the final proposed main combination, maintain per-ancestor acceptance and obtain
final human review. Do not merge #80 unchanged, rewrite migrations or infer that
individual green heads qualify a new combination.

### B00-CI-01 — prove required children ran successfully

GitHub can accept skipped/neutral required checks. App binding alone does not
prevent a changed Actions workflow reusing a required name.

**Prompt:** Add `ci-required` to CI, needing its seven mandatory jobs, and
`supply-chain-required` to the security workflow, needing its four mandatory
jobs. On PRs each completion job must run with `always()` and fail unless every
dependency result is exactly `success`. Keep original contexts, assertions,
permissions and security gates. Demonstrate pass, skipped-child, failed-child and
cancelled-child behavior with isolated fixtures; verify actual PR-triggered
successful contexts before proposing either name for required settings. Jobs
cannot depend across workflows. Do not change baseline generation or deployment
behavior, expose fork secrets, or claim these gates replace independent review.

### B00-GH-02 — Actions and deployment settings

**Prompt:** Read/export actual repository workflow permissions, outside-collaborator
approval policy and production-environment protections where authorized. Compare
them with `contents: read`, ordinary checkout credentials not persisted, no PR-job
writes, disabled Actions PR creation/approval, and the incumbent branch-only manual
baseline writer. Keep production reviewers, self-review prevention, allowed branch
and served-SHA/readiness proof separate. Report inaccessible fields as unknown;
prepare scoped setting diffs for owner review. A future nonempty PII denylist must
still fail closed without salt on restricted fork/bot runs; design a trusted inert,
SHA-bound scanning path before claiming support. Never expose scanner/deploy
secrets through untrusted `pull_request_target` execution.

### B00-GH-03 — code ownership

**Prompt:** Ask the owner to name real independent write-capable reviewers for
workflows, scanners/allowlists, manifests/locks, deployments, migrations and
CODEOWNERS itself. Prepare a CODEOWNERS file only after names and availability
are established; validate base-branch pattern coverage and permissions. Enable
code-owner enforcement only through separately authorized settings changes.
Invented names, the author and AI review prose are not independent approval.

### B00-GH-04 — prove enforcement and recover safely

**Prompt:** With explicit authorization, export settings and confirm an admin
recovery session independent of CI. Mirror the selected policy on a disposable
branch; prove missing/failed checks, unresolved threads, stale/latest-push review,
unassociated direct push, force and deletion refusal there. Read back main's
effective rules after activation, and show a current-base green reviewed PR is
mergeable without merging it. Record rule IDs, actors/roles, timestamps,
main/head/test-merge SHAs and request outcomes. Fix only a defective setting;
preserve the PR/check/force/delete baseline. The proposed one-PR, review-only,
60-minute exception is a pending owner choice, not a standing bypass. Native
bypass entries do not expire themselves. Never send destructive trial writes to
main or bypass a legitimate security failure.

### B00-CI-02 — dependency acceptance

**Prompt:** First record a current PR-base graph and a human acceptance ledger
covering inherited scope. If automatic enforcement is authorized, add a bounded
read-only dependency gate that rejects unknown/stale/unaccepted ancestors, binds
acceptance to reviewed source identities and final base, and fails closed on API
errors. Prove that retargeting alone and an unapproved ancestor do not satisfy it.
Keep it distinct from financial workflow execution and do not label it required
until its event/permission/check contract has been qualified.

## Existing failures and remaining release boundaries

The uploaded snapshot flags failed #81 and dependency PRs #88–#90. Fresh reads
confirm the following; full heads and run identities are retained in the receipt.

| PR / head prefix | Failed exact contexts |
| --- | --- |
| #81 / `686c2015f914` | `Dependency review`, `Workflow security`, `docker-build`, `lint` |
| #88 / `74b5d9e34976` | The same four, plus `backend`, `frontend-e2e` |
| #89 / `76f594ee26fa` | The same four |
| #90 / `576e294363ff` | The same four |

**B00-CI-03 prompt:** Retrieve sanitized failed logs/reports at each exact head;
separate advisories, incompatible updates, scanner/tool failures and application
regressions. Preserve original evidence and fix one bounded cause at a time with
meaningful controls. Run unchanged relevant gates and fresh eligible workflows;
do not widen severity rules, suppress audits, weaken tests, blindly accept updates
or infer a common cause from matching failed job names. Record actual root causes,
source identities and remaining failures before closing each assignment.

No failed logs were newly triaged in this intake. Green #87 does not resolve
those heads. Latest main history scan
`40621373374` completed failed at `2026-10-28T02:31:27Z`; no new classification,
count or finding-disposition claim is made here.

F10/history occurrence classification, the owner-held denylist, production served
revision/readiness, accountant acceptance and capacity remain separate. The
full EX19 recovery / EX21–22 artifacts / EX17 closure / retained finance and UI
queue is unchanged. The eight baseline PostgreSQL FK comparison differences
remain open; no foreign-key policy or drift gate is changed.

## Validation, rollback and disclosure

Executed checks are recorded in the machine receipt: safe archive inventory,
JSON parsing/proposal invariants, PDF-text comparison, source/workflow/context
comparison, independent review, links/whitespace and current-tree PII scan.
No new runtime behavior or tests are introduced. This is not GitHub-side schema
validation, an enforcement rehearsal, migration acceptance, an image re-scan or
production authorization.

Revert this documentation commit to remove the proposed backlog/configuration
intake; preserve historical evidence. No remote settings need restoration because
none were changed. Material AI assistance: file triage, source/API comparison,
independent review and documentation. Human/Lead understanding and acceptance
remain required under the existing engineering policy.
