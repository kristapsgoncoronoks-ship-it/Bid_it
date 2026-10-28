# Required-job completion checks — 28 October 2026

## Problem and delivered boundary

GitHub can accept a skipped or neutral required check. The existing eleven
application/security contexts alone do not ensure every mandatory job explicitly
finished successfully. B00-CI-01 adds two small PR completion checks without
changing their children, permissions, assertions, severity thresholds or deployment.

| Context | Same-workflow dependencies |
| --- | --- |
| `ci-required` | `pii-scan`, `lint`, `backend`, `postgres`, `frontend`, `frontend-e2e`, `docker-build` |
| `supply-chain-required` | `workflow-review`, `secret-scan`, `dependency-review`, `security-controls` |

Both use `always() && github.event_name == 'pull_request'`, a two-minute timeout
and empty job token permissions. A single inline standard-library Python step
reads `toJSON(needs)` through an environment variable, checks the complete expected
job inventory, then requires every child's result to be exactly `success`.
Missing, unexpected, malformed, failed, cancelled, skipped and other results fail.
Only fixed expected job identifiers are printed on refusal; upstream outputs are
neither executed nor logged. No checkout, external action, repository helper,
dependency install or secret is needed by either new job.

All eleven original contexts remain. Existing jobs are unchanged, including the
manual branch-only visual-baseline writer and main-push deployment's seven
dependencies. The summaries are intentionally PR-only: two existing security
children do not run on push/dispatch, and deployment does not depend on a skipped
PR summary. No application runtime, financial, tenant, schema or storage behavior
changes; no new package or framework is introduced.

## Evidence and its limits

The new contract tests execute the actual committed inline Bash/Python bodies
against realistic `needs` fixtures. Every child is exercised with non-success
results; malformed/missing/extra inventories and unexpected output data are
refused without printing outputs. The tests also retain the PR/always/dependency/
permission contract and original production/baseline boundaries. An isolated
mutant that accepts `skipped` must fail the same behavioral contract. It never
changes the candidate workflow.

All 151 new cases pass; an isolated accept-skipped mutant produces seven expected
failures. Existing supply-chain, dependency-audit, Playwright-pair, docs-truth and
compiler contracts pass 89 cases. Full collection finds 4,975 cases; it is not
local execution of the full backend suite. Pinned actionlint 1.7.12 and offline zizmor 1.30.0 inspect
the final workflows. Exact commands, counts, hashes, mutation results and source
identities are recorded in the [machine receipt](evidence/CI-REQUIRED-COMPLETION-2026-10-28.json)
and `/workspace/artifacts/ci-required-gates-2026-10-28/`.

Local fixtures prove the gate's handling of dependency results; they are not a
native GitHub scheduler/cancellation rehearsal. Forced cancellation, unavailable
runners or a missing workflow can prevent execution. A successful child job also
does not prove every internal step ran, defeat malicious workflow edits/name
reuse, or replace independent review. No branch-enforcement experiment is claimed.

Require a successful actual PR-triggered run of each new context at the delivered
source head before proposing it for branch requirements. Keep the original eleven
contexts when separately authorized settings add these names. The uploaded
byte-preserved eleven-check payloads remain unchanged proposals; neither this
code change nor green CI activates them. B00 / OPS-GOV-001, named reviewers and
Decisions section 23's operating/recovery model remain open.

## Failed-branch triage and qualified parent

Actual failed logs/reports establish these bounded causes; no new security scan,
automatic update acceptance, source edit or rerun occurred on those four heads.
Exact source/run/report hashes remain in `failed-pr-triage/` under the task
artifact directory and are bound by the machine receipt.

| PR | Finding / next action |
| --- | --- |
| #81 | Old PDF/Pillow/npm/image pins and incomplete action-version comments. Preserve the optional tooling decision; use current repaired security ancestry before final qualification. |
| #88 | Playwright library 1.64.1 versus both 1.64.0 images stops backend/browser guards before application tests. If the update is accepted, update both ordinary/visual image pins together, validate the pair and retain unchanged functional/visual assertions. Other failures inherit older main fixes. |
| #89 | Proposed pypdf 6.21.1 is below the reported 6.21.2 fix; other inherited blockers remain. Reconcile accepted updates with the current repaired stack rather than duplicating superseded fixes. |
| #90 | Pillow 13 clears its reported Pillow audit rows, while pypdf and other inherited blockers remain. Acceptance needs the actual repaired final combination. |

Current repaired ancestry already contains PDF/Pillow/follow-redirects, corrected
action-version annotations and the prior Go/npm/Alpine/RustFS image repairs. Green
individual development heads do not automatically repair old-main update PRs.
Retargeting or combining accepted updates needs fresh qualification; no waiver or
new visual baseline is authorized by these findings. Keep B00-CI-03 repairs and
B00-INT-01 integration separately open after this completed root-cause inventory.

Independent final PR #87 evidence is now verified at source `5926b84`: tested
merge tree equals source, 4,611 backend passes / 213 skips, 231 PostgreSQL,
901 retry-free functional browser, 13 ordinary visual and 19 proxy checks pass.
Ten image identities and twenty exported hashes verify zero reported
HIGH/CRITICAL; nine medium and nine unscored SBOM rows remain. Six storage groups
and dependency audit artifacts are verified. These are parent-head results, not
the new completion-check head or production acceptance. Preserved evidence:
`/workspace/artifacts/reimbursement-payment-2026-10-27/ci-completion/`.

## Maintenance prompts

**CI completion:** Maintain `ci-required` with the exact seven mandatory CI jobs,
PR-only `always()` execution, empty token permissions and strict complete-inventory/
exact-success validation. Execute its actual inline step against successful,
skipped, failed, cancelled, missing and malformed child fixtures. Prove an
accept-skipped mutant fails. Preserve original jobs and main-push deployment;
run actionlint, zizmor and existing supply-chain/docs contracts. Do not mask a
child failure or accept a nonexistent/renamed required context.

**Supply-chain completion:** Maintain `supply-chain-required` with all four
existing security jobs and the same fail-closed contract. Preserve restricted
fork/bot token/secret behavior and the independent Python/npm audit outcomes;
missing reports or skipped scanners are not clean scans. Never depend across
workflows, introduce PR write permissions or remove the original contexts. Verify
the real PR check's exact name/app/source before proposing a settings update.

**CI repair queue:** Follow B00-CI-03's exact-head failed-log evidence before
editing old branches. Inherited main dependency/image fixes already exist on
the current development stack; do not duplicate them or infer a new defect from
an old-head failure. PR #88's distinct Playwright library/image mismatch requires
paired version updates and unchanged ordinary/visual assertions on its eventual
qualified combination. The integration graph and failed update acceptance are
separate reviewable tasks.

## Failure, rollback and review

If a mandatory job is deliberately renamed/added/removed, update both the
workflow dependencies and expected inventory in a reviewed change, prove the
failure controls and verify real emitted contexts. Until then a mismatch must
remain blocked. If required settings already reference either summary, an
authorized administrator must coordinate a validated replacement before removing
its workflow job; do not strand or bypass the existing baseline. Reverting this
candidate before activation removes only the two jobs/tests/docs; there are no
data or external effects to compensate.

AI assistance covers implementation, adversarial review, test fixtures, CI
evidence and documentation. Human/Lead understanding and final acceptance remain
mandatory; neither an AI review nor passing source checks is owner approval,
integration or production authorization.
