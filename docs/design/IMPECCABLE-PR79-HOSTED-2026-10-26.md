# PR #79 — completed hosted qualification

All seven eligible core CI jobs and all four supply-chain jobs pass for immutable PR #79 head `47b28e4f6746e76c13bf13f19443425c0eb26f08`. Baseline refresh and deploy are skipped as expected for a PR. The runs completed on **23 October 2026**; this evidence was inspected on 26 October. No job was retriggered, source/reference changed, PR edited, merge or deployment performed.

CI: <https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39903413280>.
Supply chain: <https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39903413307>.

| Invocation | Completed result |
| --- | --- |
| Main backend suite | **4,535 passed / 184 skipped**, 3,549 warnings; 46m29s |
| Preliminary backend cohorts | 47 helper and15 expense checks pass; do not add potentially overlapping invocations to unique coverage |
| PostgreSQL-specific cases | **202 passed**, 322 warnings;95.14s |
| PostgreSQL growth checks | **6 passed**, 1,200 vs4,800 rows per fact table;12.46s |
| Actual expense browser/API/PostgreSQL | **9 passed**, 8.5m; targeted response-loss/read-failure injection included |
| Functional browser suite | **809 passed**, 9.3m |
| Ordinary visual assertions | **13 passed**, 9.1s; no update mode |

Logs contain exactly809,13 and9 unique progress ordinals for the corresponding browser invocations, with no reported failure/retry/flaky/skip result markers. Ordinary browser checks run with Playwright1.64.0 paired to `mcr.microsoft.com/playwright:v1.64.0-jammy` at digest `sha256:c064e7f355d2dda32f669801a1b09e86a1e9252395c844d2cfbbe5c44acb9b79`. This is normal screenshot assertion evidence against reviewed references, not a newly exported actual-image comparison. The entry bundle passes at **437.3kB raw /128.9kB gzip** against460/135.

Backend, PostgreSQL and frontend-e2e checkout logs show tested merge `93e258b0a519a427acd1faeefe527b1a4b3041ff`, with parents `97178b2…` and `47b28e4…`. Independent Git API objects prove the entire tested merge and head have the same tree: `41f5653a34442a64e0c3e841bd595ec624a68530`.

The image gate covers **ten targets**, all with **zero HIGH and zero CRITICAL rows**. All20 exported sanitized SBOM/audit hashes match the artifact summary; the artifact ZIP matches GitHub's recorded digest and passes CRC checks. Original pre-sanitization hashes are recorded upstream, but those original files are absent from the export and were not recomputed. Image report creation timestamps are23October; no fresh26October vulnerability-feed claim is made. Database feed timestamp is not exported in sanitized metadata.

Native volume qualification records **11 successful probes** (10 unique labels; two read checks), including existing-volume and legacy-refusal checks. All six S3 persistence/refusal groups pass and report cleanup. Dependency reports show no Python advisory rows and zero npm vulnerabilities; no dependency changes from base. Workflow security reports actionlint zero errors in four files, Zizmor1.30.0 success, and full40-character SHA validation success for every external action.

This qualifies only immutable PR #79. The184 skips remain explicit. Synthetic growth, storage and expense journeys do not establish production capacity, customer OCR accuracy, external delivery or accountant acceptance. Human/Lead review, main integration, planning's sibling migration graph, public readiness and deployment remain separate. New organization-recovery code requires its own qualification.

The [bounded machine receipt](evidence/IMPECCABLE-PR79-HOSTED-2026-10-26.json) records the checked identities/results. Source/run metadata, image summary/hash verification and storage status are retained in `/workspace/artifacts/organization-recovery-2026-10-26/previous-pr79/`. Raw logs, ZIPs and full exported reports remain in `/tmp/pr79-hosted-evidence`. The initial CLI log command returned empty output; direct GitHub REST log downloads supplied the verified evidence.
