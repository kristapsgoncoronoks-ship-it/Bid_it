# GitHub failure sweep — 18 October 2026

**Read directly from GitHub; no pasted logs are required.** AI-assisted analysis,
reporting and implementation. Snapshot baseline: development `5b552cb`, main
`7608c1e`. [Sanitized inventory and evidence](evidence/CI-FAILURE-SWEEP-2026-10-18.json).

**Subsequent update:** development `d404277` passed every functional job and
supply-chain; its image gate remains blocked. The hosted `final-image-security`
artifact now contains the readable `summary.md`, verified by download. The
[SQLAlchemy 2.1.4 compatibility and benchmark-startup repair](CI-DEPS-SQLA21-2026-10-18.md)
is implemented with local evidence, pending new-head CI and Lead review. The
inventory below preserves the original heads/outcomes. The rest of the backend
dependency group and the frontend candidate remain unaccepted.

## Coverage and current failures

Read all **1,039 available workflow-run records across 11 pages**, all eight branch
heads and open PR metadata. There were 549 successful, 257 failed, 168 cancelled
and 65 startup-failed historical runs. These are run outcomes, not 490 current
application defects. Cancelled/skipped/disabled runs are not passing evidence.

Of 13 latest workflow runs matching existing branch heads, **six failed workflows /
12 failed jobs** remain. All corresponding failed-job logs were read directly;
the current development image-security artifact was downloaded and inspected.

| Current head | Failed check | Finding and disposition |
| --- | --- | --- |
| Development `5b552cb` / PR #64 | Image audit, run 39133410662 | All functional and supply-chain jobs passed. Eight-target image gate remains blocked as detailed below. |
| Main `7608c1e` | Image audit, run 39012628426 | Older runtime image findings; app-runtime repairs are in the unmerged development PR. Main is not thereby fixed or released. |
| Main `7608c1e` | PII history, run 39090755978 | Existing V07 historical classification/owner-denylist work. A clean tree cannot clear history; no rewrite or identifier disclosure performed. |
| Backend dependency PR #63 `a362cf5` | Backend, PG, mypy, image audit; run 39012871330 | SQLAlchemy 2.1 changes tuple-result typing/deprecations and tenant-predicate compilation. Mypy reports 115 errors; warnings-as-errors and the reach-rule assertion also fail. Current branch pins 2.0.52 and passes functional checks; accepting 2.1 needs a compatibility repair, not disabling those controls. |
| Frontend dependency PR #61 `8d597cd` | Backend, audit, bundle, browser; run 38614170715 | Old calendar parity failure and nine dependency advisories; bundle 462.2/135.4 kB exceeds 460/135; Playwright library 1.63.0 disagrees with image 1.62.1. Current candidate addresses older failures at its own versions; PR #61 remains red until integrated/requalified. |
| Same PR #61 | Workflow security, run 38614170433 | Six action hash/version-comment mismatches. Current development workflow pins/comments passed supply-chain CI; this older branch has not inherited that evidence. |

**Subsequent backend repair:** SQLAlchemy compatibility and the other nine updates
are integrated with warning, type and RLS controls retained.
[Decimal semantic diff and full-vector qualification](CI-DEPS-BACKEND-REST-2026-10-18.md)
record 3,934 passing backend and 131 PostgreSQL checks, shipped-runtime and
same-database financial-command replay. The generated contract awaits Lead review.
**Subsequent frontend repair:** the exact lock and matching Playwright 1.63 pair
are integrated, with [bundle/recovery/browser/native qualification](CI-DEPS-FRONTEND-2026-10-18.md).
The first-load gate now passes at 434.3/128.2 kB under unchanged 460/135 limits.
Generated visual baselines are unchanged; exact-head CI/Lead review remain.
Do not blindly merge older dependency branches or claim their failures
vanished because another head passed.

## Current image blocker

The downloaded artifact has **eight targets, 47 advisory/package rows and three
unavailable references**. Final API/origin are clean. Backend builder: four HIGH;
frontend builder: 18 HIGH; PostgreSQL gosu dependencies: 24 HIGH/one CRITICAL.
This is not a PostgreSQL query-engine vulnerability verdict.

Pip **26.2.1**, npm **12.2.0**, TypeScript **7.0.2** were rechecked against their
verified HTTPS registries and remain latest. Some bundled dependencies have known
fixed versions but no supported containing-tool release; one advisory has no
listed fix. Manually replacing vendor directories or silently downgrading the
compiler does not qualify a supported build. Existing Decisions §29 records
vendor/Lead qualification. MinIO/mc are unavailable and the community source is
unmaintained; choosing support/licensing/provider/restore terms remains an owner
decision. PgBouncer's configured tag is unavailable; a real supported image still
needs port/configuration/read-only-root qualification before selection.

### Implemented CI reporting repair

The image audit now writes **summary.md** into its existing evidence artifact and
publishes the same report to **GITHUB_STEP_SUMMARY**. It lists each image/role,
scan status and advisory count, then severity, advisory, package, installed and
scanner-reported fixed version. Unavailable/unverified targets say **not scanned**,
not zero. Inventory failures publish a blocked summary too. Metadata is redacted
and escaped. Large tables explicitly show 100 of N rows per image; full JSON/SBOM
artifacts retain all rows. No scanner, severity, source-pinning, exit-code or
coverage requirement changed.

Four pre-fix report regressions failed. **43 reporting/supply-chain checks passed**, including seeded
vulnerability, missing/malformed evidence, bad inventory, unsafe metadata and
explicit preview counts. Replaying the actual downloaded artifact renders all
eight targets and 47 rows while retaining **BLOCKED**. New-head GitHub publication
is pending CI; local rendering does not claim it already appeared online.

## Review and remaining work

**Proposal/benefit:** put verified image findings beside the failed GitHub job so
an operator can see the cause without opening a ZIP. JSON-only artifacts preserve
evidence but provide a poor first diagnostic. **Adversarial objection:** a preview
could hide findings or imply an unavailable target is clean. Counts/truncation are
explicit, complete artifacts remain, and seeded failures retain nonzero exits.
**Complexity:** a renderer plus report writer, no new dependency or workflow
permission. **Lead decision: pending** on reporting/redaction and control evidence.
AI-assisted code/tests/reporting; human review and exact-head CI remain required. Rollback the report renderer/writer and added advisory projection to
restore the former JSON-only reporting; enforcement stays fail-closed either way.
No historical run was relabelled, gate bypassed, image replaced, shared history
rewritten or production deployment performed. This is a completed inventory and
reporting repair, not a claim that every workflow is green.
