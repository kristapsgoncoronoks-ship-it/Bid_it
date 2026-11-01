# Patch newly reported dependencies without weakening CI

PR #99's initial source `026b7feb6c9436edee285a1b18432e023637ac93` passed
local EX17 feature tests but failed fresh hosted auditing. Core run
[`41296979367`](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/41296979367)
passed Ruff/format/mypy, then failed pip-audit; supply run
[`41296979353`](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/41296979353)
independently failed dependency review. Its required summary correctly refused
the failed result. The tested merge and source trees matched. These are real
blocked gates, not regressions established in the new reimbursement logic.

The original final-image job subsequently failed on the same dependencies:
15 HIGH finding rows across eight advisory IDs. Seven pypdf IDs appear in each
of backend final/build, and the follow-redirects advisory (CVE-2026-116689) appears
in frontend-build. Seven other image roles have no gate findings and inventory
errors are empty. Storage qualification was skipped after that failure; it was
not a successful run. Retain the saved image reports and their source identity.

PR #98's completed 29 October CI remains a historical successful run. It does
not negate advisories newly reported when the same dependencies were audited
on 1 November. Original red logs/reports and later qualification are separate
under `/workspace/artifacts/reimbursement-terminal-boundary-2026-11-01/ci/`.

## Minimal repair and applicability

| Dependency | Observed advisory / repair |
| --- | --- |
| pypdf 6.21.2 | Seven HIGH advisory IDs: CVE-2026-119203–119209. Three require 6.21.4; four list 6.21.3. Pin 6.21.4, the minimal common patched version. |
| follow-redirects 1.16.1 | HIGH GHSA-mrwp-f3q8-3g9p affects 1.16.0–1.16.1; patched in 1.16.2. The npm report has two affected package rows (Axios and follow-redirects), representing one advisory. |

The Python change is one requirements pin/comment. Published PyPI wheel SHA-256
was independently verified; Python ≥3.9, BSD-3-Clause license and declared
dependency requirements are unchanged. Installing the verified wheel changes
only pypdf in the app virtualenv; the dependency consistency check passes.

The npm change is only the existing lock entry's version, tarball URL and SRI.
The patch satisfies Axios's existing `^1.16.0` range. No override, root dependency
or graph expansion is added. Registry SHA-512/SRI and downloaded tarball match.
Six unrelated platform metadata fields removed by npm's regeneration were
restored to keep the source diff exact. `npm ci` succeeds against the final lock.

A synthetic two-origin loopback probe reproduces custom-header forwarding in
the old direct redirect package and verifies the patched object/array-header
variants, preserving built-in auth/cookie protections and harmless headers.
The advisory specifically says Axios 1.20.0 did not reproduce because it
normalizes the header array. This is an inventory repair and direct-library
regression control, not evidence of Bid_it leaking real credentials. No customer
input, provider or external payment is used.

## Executed qualification and limits

Local Python requirements auditing reports 78 dependencies and zero advisory
rows; npm auditing reports 188 dependencies and zero advisories. No ignores,
severity waiver, scanner fallback or gate change is used. PDF compatibility has
131 passing cases without skips; generated-contract/supply-chain checks have
19 passes. Three bounded valid/encrypted/malformed PDF probes pass. Frontend lint/build, 19 proxy checks and the unchanged bundle budget
pass; the eight direct-package loopback controls pass.

Patched-source full browser and connected payout rerun results are finalized in
the [machine receipt](../reviews/evidence/CI-DEPENDENCY-REPAIR-2026-11-01.json).
The final traced run passed all 949 unique functional cases, one passed attempt
each, with zero skips/retries/flakes/runner errors in 506.95s. The new connected
browser/API/PostgreSQL journey passed in 66.26s with no route mocks; its six
terminal-action denials preserve committed snapshots and its distinct checker
records one canonical payment. Original/current payment readback matches.
Six source/dependency hashes match before and after the reruns. Tracing is
diagnostic overhead; this is not a speed comparison or production sizing claim.
The initial EX17 949-case run and connected journey remain bound to their old
dependency hashes. Do not substitute those results for repaired-source tests.
The same financial/UI files and all prior migration history remain unchanged;
this repair creates no model or schema difference.

**CI-DEV-LOAD-01:** the first patched full run passed 948/949; suspended-workspace
navigation hit a global workspace `PageLoadError`. Its exact module/transport
cause was not preserved, so this is not established as a Billing or optimizer
defect. An isolated-cache instrumented cohort passes 30 cases, and ten explicit
fresh-context executions of the exact scenario pass with retries disabled.
Three deliberate module-failure controls capture their expected causes, proving
the diagnostic can observe this failure class. Original assertions/fixtures and
shared optimizer metadata remain unchanged. Preserve the failure as an open
observation; a final full run uses retain-on-failure tracing without weakening
assertions/timeouts. Passing reruns do not identify or erase the original cause.

Initial writable-cache refusals and an accidental empty root npm lock from a
wrong-directory invocation are retained as setup evidence. That exact empty
file was backed up and removed; no unrelated lock or source edit remains. These
are not product regressions. Original failed hosted auditing is never relabelled
as passing, and fresh delivered-head CI/human review remain required.

## Rollback and maintenance prompt

Keep the patched versions and existing security gates. Application rollback may
retain these compatible patches; do not restore known-vulnerable pins merely to
revert EX17. A future advisory needs its own applicability/fixed-version review,
minimal graph delta, integrity and relevant compatibility/regression evidence,
followed by auditing and exact-source CI. A clean audit is a dated inventory
observation, not a vulnerability-free or production-release guarantee.

AI-assisted implementation and independent security review; human/Lead review
remains pending. No main merge, repository-setting change, production migration,
provider enablement or deployment occurred. RB04 retention/access/replacement,
RB05/EX22 payment allocation, EX21, full EX19 and release controls stay open.
