# PR #78 — completed hosted qualification

All seven eligible core CI jobs and all four supply-chain jobs pass for PR #78
head `97178b2f5d26e3f3d8bf908060ff249bd95254aa`. The baseline-refresh and deploy
jobs are skipped, as expected. These runs completed on 22 October; this receipt
was inspected on 23 October. No job was retriggered and no repository source,
documentation or accepted visual reference was changed during this review.

CI run: <https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39732018285>.
Supply-chain run: <https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39732018254>.

## What actually ran

| Invocation | Completed result |
| --- | --- |
| Main backend suite | 4,530 passed; 184 skipped; 3,539 warnings; 27m11s |
| Preliminary backend helper / expense cohorts | 47 and 15 passed, respectively; overlap is possible, so these are not added to the main suite |
| PostgreSQL-specific cases | 202 passed; 322 warnings |
| PostgreSQL growth checks | 6 passed, measuring 1,200 versus 4,800 rows per fact table |
| Actual expense browser/API/PostgreSQL journeys | 9 passed; includes targeted response-loss/read-failure injection |
| Functional browser suite | 781 passed in 8.9m |
| Ordinary visual regression | 13 passed in 9s, without update mode |

The browser log contains exactly 781 unique functional progress ordinals and
13 unique visual ordinals; the live suite contains nine unique ordinals. No
failed/flaky/retry result marker appears in those logs. This does not explain
the earlier local lazy-import failure; it remains separately recorded.

The normal visual run used Playwright **1.64.0**, paired successfully with
`mcr.microsoft.com/playwright:v1.64.0-jammy` at digest
`sha256:c064e7f355d2dda32f669801a1b09e86a1e9252395c844d2cfbbe5c44acb9b79`.
This is ordinary assertion evidence against the reviewed references; it does
not claim newly exported actual screenshot bytes are identical to references.
The production entry bundle passes at **436.3 kB raw / 128.7 kB gzip**, against
460 / 135 kB.

## Tested-source identity

Completed job logs show checkout of the PR merge commit
`28ecccb1139b5f1bc294957207fa38367fe7353b`, with parents PR #77's `60e1c223…`
and the reviewed head `97178b2…`. Independent Git API responses prove that
the entire merge tree and head tree are identical:
`bac975a7d290f89bb23e8cd013e00841cfa41d35`. Thus the results qualify those
same repository bytes, rather than merely assuming a head/merge equivalence.

## Image and storage evidence

The final-image security artifact contains **ten targets**, all clean at the
gate's HIGH/CRITICAL severity scope: four application/final targets, five build
targets and the pinned RustFS service. Independent parsing finds **zero HIGH
and zero CRITICAL rows**. All **20 exported sanitized SBOM/audit report hashes**
match the summary. The artifact ZIP also matches GitHub's recorded digest and
passes CRC integrity checking. Original pre-sanitization report hashes are
recorded upstream, but those original raw files are not shipped and were not
independently recomputed.

All six native volume/storage groups and all six native S3 persistence/refusal
groups pass, including new-server/same-volume preservation. These synthetic
checks do not establish production capacity. Dependency, workflow security,
credential and control-register checks also pass; the dependency logs report
no known Python advisories and zero npm vulnerabilities.

## Limits and handoff

This qualifies the preceding immutable candidate only. The 184 backend skips
remain explicit; results from different suites are not summed into unique
coverage. Vulnerability results describe the scan's feed and severity scope,
not a timeless absence of vulnerabilities. Human/Lead review, main integration,
the sibling planning migration graph, customer invoice OCR acceptance, public
readiness and deployment remain separate. A new candidate needs its own checks.

Machine evidence: [receipt](evidence/IMPECCABLE-PR78-HOSTED-2026-10-23.json), `image-exported-report-verification.json`,
run/commit metadata, `image-summary.json` and `storage-smoke.json`. Raw logs and
the downloaded image artifact remain in `/tmp/pr78-hosted-evidence`. The prior
pending-at-handoff visual receipt is preserved unchanged; this completed record
supersedes that status without rewriting its history.
