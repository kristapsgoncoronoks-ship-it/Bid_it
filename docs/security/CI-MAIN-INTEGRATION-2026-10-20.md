# Main CI integration — 20 October 2026

**Follow-up after the user's instruction to proceed:** the documented Hostinger
stack uses local storage; the S3 provider refusal is a separate deployment shape.
Source review found a concrete writer-quiescence gap in its rollout script.
[The repair and tests](CI-VPS-WRITER-QUIESCE-2026-10-20.md) are implemented;
fresh hosted checks must qualify the new commit before normal PR integration.
Existing merge authorization is reaffirmed, not a statement that `DEPLOY_ENABLED`
is false. The earlier setting question and green pre-repair evidence below are
preserved as dated observations, not the current sole integration gate.

**The repair passes fresh main-target checks in [draft PR #67](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/pull/67). Main itself is unchanged and its older image gate remains red.**

Candidate `22c9b3afbfbeb81296a7c06490da9c855aa2d730` combines the existing
development and expense repairs. Main is still
`7608c1e6335a464476d548bfbb28d3fda3ba499f`. The main comparison contains
46 commits and 245 changed files; its application, storage and financial scope
is described in the PR. No source change was needed after PR #66's qualification.
Earlier uncommitted UI/password/directory/AP changes remain in the original
checkout and are outside this candidate.

## Why the reported run failed

The manually dispatched [main run 39383821339](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39383821339)
ran the older main revision. Six required jobs passed; `docker-build` failed at
“Final image SBOMs and vulnerability gates.” Its obsolete two-image reports have
71 backend package/advisory rows (70 HIGH, one CRITICAL) and two frontend HIGH
rows. These match the earlier retained baseline's finding tuples. They are not
73 distinct advisories or evidence that the integration repair regressed.

The failed run's artifact is `12245983769`, with ZIP SHA-256
`8cd3c9c02dc23a111198ef53d792fc5b4f671b1ce06d54fb52710c96f2c433c6`.
The backend critical row concerns `libxml2` / `CVE-2026-6653`; that artifact
lists no fixed version. The frontend findings concern `libexpat` and `libx11`.
The combined candidate already replaces and qualifies these image paths.

## Fresh checks against main

[CI 39387234420](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39387234420)
and [supply-chain 39387234324](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39387234324)
both completed successfully on their first attempt. Retained checkout logs
explicitly fetch `refs/pull/67/merge`, synthetic commit
`e3c772068ff0f5ba794cfa8e53391e8ecc901e8a`, combining candidate `22c9b3af`
with main `7608c1e`. This is fresh main-context proof, separate from PR #66's
earlier expense-target checks.

| Check | Completed evidence |
| --- | --- |
| Required CI | All seven jobs passed: tree PII, lint, backend, PostgreSQL, frontend, frontend E2E and Docker |
| Supply chain | All four jobs passed: workflow security, dependency review, control register and generic credential scan |
| Backend | 4,361 passed / 156 skipped / 3,472 warnings, 1,983.30 seconds; earlier focused checks overlap this suite |
| PostgreSQL | 174 passed; six separate performance checks and six live expense-browser cases also passed |
| Frontend | 620 functional browser cases and 13 visual checks passed; existing baselines preserved |
| Bundle | 434.6 kB raw / 128.3 kB gzip, below unchanged 460/135 limits |
| Host compiler | Exact source `2bd066d87f5bafd315be9f40889d0a60b9e58e0b`, version 7.0.2, Go 1.27.2; native executable SHA-256 matches prior qualification |
| Images | Ten targets, zero HIGH/CRITICAL findings; all 20 retained sanitized SBOM/audit hashes independently verified |
| Native storage | All six groups passed, cleanup confirmed; backend/server image identities agree with image-gate evidence; Python 3.14.8 / UID 10001 |

Counts overlap across suites and must not be summed into one denominator.
Skipped optional baseline refresh and deployment are expected for this PR event.
A skipped PR deploy job does not establish that main auto-deployment is disabled.

Fresh image artifact `12247594701` has 54 files. Its retained ZIP SHA-256
`8a7f022458b3e88542d895a006283a70bee390c8c9878ca413a8bec3ffa4f133`
matches GitHub's recorded digest. The exact-head inventory contains ten targets
and 15 source occurrences. Four retained completed-job log hashes also match.
The artifact's hosted retention expires on 27 October 2026 at 07:43:57 UTC.
Source-scoped summaries, integrity hashes and independent review are retained in
[the cumulative sanitized evidence](evidence/CI-SCAN-AND-REPAIR-2026-10-20.json).

These reports establish the stated HIGH/CRITICAL gate outcome, not absence of
all vulnerabilities. Raw pre-sanitization reports were not retained, so their
producer hashes are not independently verified. The storage report does not
contain Docker ImageConfig/label fields; those fields are not claimed verified.
Consistency checks are not independent signatures, production sizing or
provider/cutover certification.

## Remaining integration condition

The user has already authorized merging possible branches. [Decisions §23](../DECISIONS-NEEDED.md#23-branch-protection-on-main--the-exact-setting-pngx-p1-01--ops-gov-001-p1)
requires a PR and green checks, and explicitly says “no second approval while
the team is one person.” A missing second GitHub approval is not being invented
as an additional gate. No human acceptance or production release is asserted.

Whether merging triggers a live deployment is still unknown. GitHub returns
403 for the repository's `DEPLOY_ENABLED` variable and main protection settings;
neither absence nor a false value can be inferred. The candidate deploy job
runs on a push to main only when `DEPLOY_ENABLED == 'true'`, after required CI.
The user has been asked whether main automatically deploys the live app. The
merge remains pending that operational fact; no settings were changed or
bypassed and no merge or deployment was performed.

Production provider/storage cutover remains [Decision §29](../DECISIONS-NEEDED.md#29-residual-nativebuild-image-findings-v05b-2026-10-16).
Both production bootstrap actions deliberately refuse pending that decision.
Development RustFS qualification does not select a production provider or prove
historical-data migration. The historical-PII workflow's 872 structural matches
also remain owner-dependent under Decision §24. Main integration does not clear
those findings, the newer daily report's open action contracts or local UI work.

The original [CI scan and repairs](CI-SCAN-AND-REPAIR-2026-10-20.md), PR #66
qualification and old failed runs retain their dated scope. Reverting this
integration would restore the known red image baseline, not a qualified
production fallback. No audit threshold, required check, permission, upload
limit or visual baseline was weakened.
