# Proposed main-branch protection — 28 October 2026

**Not applied or approved.** These are byte-preserved configuration proposals
from the user's uploaded bundle, not executable setup instructions. Importing
them does not authorize repository settings, merges, review exceptions or deployment.

Normal proposed configuration uses both
[baseline](main-baseline.ruleset.json) and
[independent-review overlay](main-independent-review.ruleset.json).
They target only `refs/heads/main`, have empty bypass lists, require merge
commits, and combine their restrictions. The baseline requires eleven exact
GitHub Actions check contexts, a current base, PRs and resolved threads; it
blocks force pushes and deletion. Its approval count is zero because the
overlay separately requires one independent approval, stale-review dismissal
and approval of the latest reviewable push.

An eligible independent human reviewer must exist before enabling the overlay.
Baseline-only solo operation is a separate owner decision with lower review
assurance. Do not infer either choice from the uploaded files. The
[classic protection body](classic-branch-protection-fallback.json) is an
alternative, not an additional required rule; its merge-method setting is
separate and it also assumes an eligible reviewer.

Before any authorized use, refresh and export live settings, preserve stronger
controls, verify the reviewer/recovery plan, rehearse on an approved disposable
branch, then read back effective enforcement and test both refused and allowed
paths. No GitHub schema-validation/apply endpoint or enforcement experiment has
been performed for these payloads. JSON parsing and observed check-name/app-ID
agreement do not establish enforcement.

[Review, exact action prompts, evidence and remaining decisions](../../../reviews/BRANCH-PROTECTION-INTAKE-2026-10-28.md).
The original PDFs, source JSON and read-only receipts remain in the task artifact
directory identified there. No uploaded script is executed.
