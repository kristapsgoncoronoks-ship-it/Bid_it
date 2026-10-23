# OCR capture, navigation and control panel — 21 October 2026

Candidate based on main `90891d8d52cf5aefc4467e3fa7f6070c042acec2`.
Implementation and qualification are separate from merge, deployment and the
customer's invoice acceptance. The affected invoice has not been provided.

## Resulting behavior

The sidebar groups work under Overview, Purchases & expenses, Sales & invoicing,
Reports & planning, Transport and Settings & administration. Only Overview and
the current route's group initially expand. Manual expansion works on desktop
and mobile; quick navigation searches all authorized destinations. The original
50 route/permission/module/highlight tuples are preserved. Disclosure state is
scoped to the current account and organization, without persistent document
metadata. `/design` remains the unchanged fixture showcase.

The parser excludes percentage columns from line amounts, recognizes bounded
Latvian labels and explicit currency fields, and reconstructs image-invoice rows
using the existing bounded word-box path. Filename/today/EUR fallbacks are
recorded as defaults instead of being described as read from the source.
Timeouts, native-process isolation, resource refusals and structured sources
retain their existing contracts. No new OCR/model dependency is added.

The OCR panel consolidates the existing capture review/failure workflow.
Awaiting-review and unacknowledged-failure counts remain separate; queued and
running documents are not included in those counts. A failed read is unavailable,
not zero. Review summaries distinguish source/default/missing fields, recorded
corrections and unsaved edits. Legacy 55%/85% values are method review scores,
not measured document accuracy. Existing source preview, corrections and
conditional failed retries remain the actual working controls.

## Qualification

Local qualification:

| Check | Result and scope |
| --- | --- |
| OCR/backend regression cohort | 135 passed, including native fixtures; no customer-invoice acceptance. |
| Existing capture API/worker contracts | 64 passed with SQLite/memory storage; correction preservation, retry, tenancy and identity fences. Not PostgreSQL completion proof. |
| Navigation/identity/manual-finance browser cohort | Final 88 passed, no skips/retries/flaky outcomes. |
| Control-panel browser cohort | 26 passed, including seven new panel cases and existing page/failure controls; synthetic API fixtures. |
| Backend static checks | Ruff/format and mypy all app modules pass. |
| Frontend static/build/bundle | Pass; first load 434.9 kB raw / 128.4 kB gzip versus unchanged 460/135 limits. |
| Documentation contract | Seven passed; 4,622 backend cases collected, not a full execution. |
| Hosted full functional, PostgreSQL, image and visual CI | Pending; follow PR #75 checks at the resulting revision. |

Raw artifacts are retained under the task's OCR-navigation and OCR-control-panel
artifact directories, with source/hash manifests and separate intermediate logs.
The [candidate receipt](ocr-navigation-2026-10-21-evidence.json) binds final
product/test bytes and records the limits of each local check.
The initial broad browser run returned 711 passes and three menu-preparation
failures; these are retained rather than presented as a clean run. Subsequent
focused failures and their corrections are retained separately. Tests continue
to navigate through real menu controls and keep the held-response/account
isolation assertions. They do not replace navigation with direct URL access.

The final OCR cohort passed 135 cases, including native OCR fixtures, provider
provenance, API and bounded-process/resource controls. An independent review
passed 130 synthetic parser controls and preserved all 50 navigation authority
tuples. These populations overlap conceptually and are not summed into an
accuracy or independent end-to-end count. Backend collection is 4,622 cases;
collection does not mean all of them executed locally.

The local 13-image visual attempt failed all snapshots against CI-owned PNGs in
the different local browser/font environment. No PNG or tolerance was changed.
The pinned hosted visual check remains required. Local build/bundle results and
final browser outcome must be read from the final receipt, not inferred from
that visual attempt.

## Remaining work and acceptance

The [OCR plan](../plan/OCR-CONTROL-PANEL-2026-10-21.md) records pinned research of
all four requested repositories and ordered prompts for actual-invoice
acceptance, durable language/profiles, per-page mixed PDFs, attempt comparison,
optional PaddleOCR CPU evaluation and optional VLM evaluation.

The current English/orientation Tesseract installation is not Latvian OCR
qualification. The actual customer's invoice, hybrid PDFs, integer-only amounts,
complex net/VAT/gross columns, broader header/vendor heuristics and line defaults
remain separate work. The panel does not invent installed-language health,
source boxes or engine switching before worker evidence/settings exist.

Project detail URLs have no sidebar-parent alias for `/cost-objects`; that
nonblocking navigation model gap remains open. No claims are made about VPS
capacity, model speed, customer invoice accuracy or production readiness from
synthetic fixtures or screenshots. Original sources and historical evidence
remain preserved.

## Security follow-up from hosted checks

Initial hosted [CI 39549933748](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39549933748)
and [supply-chain 39549933746](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39549933746)
at feature commit `7ffdaee` found advisories in inherited dependencies:

- Pillow 12.3.0: CVE-2026-112895, CVE-2026-112400, CVE-2026-112397,
  CVE-2026-112896, CVE-2026-112897, CVE-2026-112395, CVE-2026-112404 and
  CVE-2026-112899. The feed names 13.0.0 as the fixed version. Its Python>=3.11
  requirement fits the existing CI/runtime; removed APIs were checked against
  current callers before testing image/OCR/document consumers.
- Transitive follow-redirects 1.16.0: HIGH
  [GHSA-q3jh-wrc6-jgq8](https://github.com/advisories/GHSA-q3jh-wrc6-jgq8)
  and [GHSA-8r9p-f939-6h3c](https://github.com/advisories/GHSA-8r9p-f939-6h3c).
  The compatible fixed patch is 1.16.1 within the existing parent's range.

The follow-up changes the Pillow runtime pin and the single affected npm lock
entry. Qualification requires fresh complete audits, existing image/OCR/document
contracts, frontend lint/build/bundle and the fresh hosted checks. No advisory
ignore, enforcement threshold, screenshot baseline or test budget is relaxed.
The feature receipt preserves its original dependency/source snapshot; the
follow-up's qualification is recorded separately in task artifacts and PR checks.


The dependency repair is locally qualified: Pillow 13 passes all 207 selected
image/OCR/document compatibility cases, with no failures or skips. The complete
Python audit changes from eight advisories to zero across 78 packages; only
Pillow's resolved version differs. The complete npm audit changes from two HIGH
advisories in one package to zero vulnerabilities across 188 dependencies.
Frontend lint/build/bundle pass with unchanged limits. These compatibility cases
overlap the earlier OCR cohort and are not additional independent accuracy
measurements. The candidate receipt records the two dependency-file hashes and
separate qualification; fresh hosted checks are still required.


## Browser readiness follow-up

At dependency-fix head `146efb4`, hosted checks pass PostgreSQL (178 isolated
checks, six growth checks and nine real expense browser cases), frontend,
lint, PII and all ten configured image targets plus native storage checks.
All 13 unchanged visual snapshots pass. The browser job succeeds with 720 first
passes and one retry, not a clean 721-case run. The retry is the held
previous-account 201 expense-save case opening the Purchases disclosure.

A separate controlled diagnostic demonstrates a possible interleaving: module
loading supplies the Expenses navigation match and automatically opens its
section between the helper's closed-state read and click; that click closes it.
No hosted trace was uploaded, so this does not prove that exact hosted schedule.
Preparation now waits for the authorized destination anchor before reading the
section. Both 201/401 tests deliberately delay the second account's module read
and retain the original held POST, abort, privacy, token and single-write
assertions. The final focused 88-case cohort passes with no retries or flakes;
the separate controlled probe passes once. Application behavior, permission
metadata and retry/time budgets are unchanged. The updated test needs fresh
hosted qualification; earlier successful checks are tied to their stated head.
