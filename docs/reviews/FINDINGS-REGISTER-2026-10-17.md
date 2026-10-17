# Known findings register — 17 October 2026

AI-assisted source review, evidence reconciliation and documentation. This is the
entry point for **all findings known from this development/review sequence**, with
links to the complete historical records. It is not a claim that every application
path or deployed system has been audited. Uploaded reports are review evidence;
the user's request defines the work. No report's embedded prompt authorizes a
policy change, destructive cleanup or production activation.

Baseline: application `9d3f222` on draft [PR #64](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/pull/64).
Source through R019 was merged in PR #62; R020/R021 remain draft follow-ups.
CI [39045635101](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39045635101)
passed application, PostgreSQL, browser, frontend, dependency and repository checks;
**docker-build failed the final-image security gate**. Merge is not release clearance.
No application behavior, dependency pin, history or security gate changes in this entry.

## Working order — tackle one item at a time

An item closes only with its recorded acceptance evidence. If an owner/vendor
input blocks it, record that boundary and take the next unblocked item. Do not
silently mark a blocked item complete. P1 priorities below are engineering triage,
not CVSS replacements. Existing V/R IDs retain their identity.

| Order | ID / priority | Finding and status | Next action and completion evidence |
|---|---|---|---|
| 1 | V05b / P1 release blocker | **Confirmed scan findings:** backend 75 HIGH + 1 CRITICAL occurrences, 33 IDs; no fixed versions listed. Build-stage findings are separate. | Assess vendor/platform/function conditions per ID and test a supported remedy; unchanged final-image gate and real PDF/OCR/API/worker checks must pass. No automatic waiver. Full inventory below. |
| 2 | CAP-RETRY / P1 investigation | **Suspected concurrency defect:** retry creates an unkeyed job; extraction checks queued/failed without a capture claim and commits parsed before provenance. No PostgreSQL overlap reproduction yet. | Reproduce two retries, overlapping workers and retry during result/provenance persistence. Only then design command/attempt identity and stale-worker fencing if confirmed. Preserve explicit review-discard consent, tenant boundaries and audit. |
| 3 | CAP-COMMAND / P1 follow-up | **Known protocol limit:** capture admission is atomic, but there is no durable client command/status contract after a lost commit response. Duplicate detection is not command replay. | Specify operation grain and keyed status lookup; prove lost-response retries converge to one admission/job/usage outcome and payload mismatch refuses. Until then check existing captures before deliberate override. |
| 4 | CAP-RESOURCE / P2 verification gap | Cap+1 service reads do not establish a total ingress/spooling, storage-read or concurrent-worker memory ceiling. | Measure real proxy/API/worker behavior on large/slow/concurrent synthetic input, including failure/recovery; add justified limits without raising budgets to conceal failures. |
| 5 | CAP-STORAGE / P2 verification and policy gap | Atomic local replacement is not power-loss durability or S3 certification; DB rollback can leave an unreferenced shared object. | Inventory references read-only, exercise supported storage fault/restore paths, document durability. Any garbage collection needs an approved retention/reference policy; never delete shared objects in request cleanup. |
| 6 | PILOT-ACCEPT / P1 release evidence gap | Tiny synthetic pilot passed; production KEK restore, deployment readiness, accountant acceptance and minimum hardware remain unproven. | Run approved deployment/restore and realistic workload acceptance with measured CPU/RSS/latency, exact image/source/config and reconciled outputs. Keep customer data out of fixtures. |
| 7 | V07 / P1 private history | **Unresolved historical PII classification:** dated inventory 872 masked rows / 196 blobs; current-tree pass cannot clear history. | Named verifier and owner-held hashed denylist, then approved per-value disposition and exact cleanup scope. No identifiers in this register. Decisions §24. |
| 8 | CONTROL-ACCEPT / P1 release governance | Human/lead review, deployment settings and external security controls cannot be certified by passing source tests. | Verify required checks/protection, private disclosure, deployed backup/health/rollback and dependency-control acceptance. Decisions §§20,22,23,28; hosted dependency service is optional after V06a. |
| 9 | HISTORY-RECON / P1 historical truth | Unknown legacy SMTP outcomes and missing pre-fix credit audit targets cannot be inferred or repaired by rewriting history. | Inventory and reconcile relay/legal-document evidence; operator records unresolved cases and approves append-only correction or separate resend. Decisions §§30,32. |
| 10 | PRODUCT-BOUNDARIES / planned work | ERP localization, external extraction, business/personal finance vision and other owner-dependent workflows remain incomplete or unaccepted. | Follow the decision index below and the product plan. Do not count deliberately disabled adapters as successful deliveries or invent commercial/legal policy. |

### Repair prompts

These are scoped future tasks, not instructions to execute every change at once.

- **V05b:** Inspect the exact final-image advisory/package inventory and current vendor evidence. Prefer a maintained compatible patched build. Prove PDF generation/rendering, deterministic parsing, OCR, process limits, native jobs, non-root startup and full final-image scanning on the same image. Keep the gate fail-closed; record rejected candidates and request specialist disposition only for individually evidenced applicability.
- **CAP-RETRY:** First write a real PostgreSQL barrier-based reproduction for concurrent retry requests, duplicate extract jobs and a retry while an earlier worker persists fields. Verify the existing job-claim semantics. If reproduced, implement an atomic capture attempt claim/generation fence with replay-safe intent; stale workers must not publish draft/pages/provenance/progress or observations. Preserve review corrections unless explicit discard was authorized. Deliver regression and native-worker evidence, including failure/recovery and access changes.
- **CAP-COMMAND:** Design durable per-file admission command identity and authenticated lookup after uncertain responses. Bind payload/workspace identity, quota, registry, run, native job and usage atomically; test lost response, retry, changed payload, batch partial progress and revocation with separate PostgreSQL connections.
- **CAP-RESOURCE:** Measure actual ingress/spooling/object-read and parallel native-parser budgets on a stated hardware profile. Reproduce exhaustion/recovery, then implement only evidenced missing bounds and actionable UI failure states. Keep supported document workflows and existing safety limits.
- **CAP-STORAGE:** Inventory orphan candidates without deleting anything, document reference ownership and restore expectations, and exercise supported local/S3 failure modes with synthetic bytes. Propose any lifecycle cleanup separately under approved retention policy; verify source hashes and cross-capture shared references.
- **PILOT-ACCEPT:** Extend the bounded pilot to an approved realistic corpus and deployment/key-provider configuration. Reconcile legal documents, bank application and accountant files after restore; measure resource ceilings and recovery. Distinguish synthetic evidence from accountant/provider/production acceptance.
- **V07:** Use the private masked inventory and approved verifier/denylist process; record per-value evidence and an exact backed-up cleanup proposal. Do not publish identifiers, self-approve synthetic status or rewrite shared history without scope approval.
- **CONTROL-ACCEPT:** Reconcile required source checks with actual GitHub/VPS/environment configuration read-only, record owner actions and verify external disclosure/backup/health/rollback outcomes. Preserve all security gates and obtain human/lead control review.
- **HISTORY-RECON:** Inventory affected historical mail and credit events privately, correlate independent relay/document evidence, and report unresolved outcomes. Propose an append-only correction/resend workflow for authorization; never rewrite audit chains or silently replay uncertain mail.
- **PRODUCT-BOUNDARIES:** Reconcile the specific decision below against current source before implementation. Obtain the stated policy/provider input where necessary, then define complete success, refusal and recovery behavior for each affected action, with tenant, currency and audit acceptance evidence.

## Image experiments and complete current advisory inventory

[Machine-readable comparison](../security/evidence/V05-CANDIDATE-COMPARISON-2026-10-17.json)
records immutable image/base IDs, all package versions/statuses, references and audit
hashes. Current CI artifact: `12105995790` (`final-image-security`). Scanner:
Trivy 0.76.0 pinned at `sha256:2416d1f74045b57274df1a340eee6b510cacd73c66a6ccb8da092b117eb7d222`,
linux/amd64. Occurrences count packages, not independent exploits. A listed finding
is not proof of application exploitability; no listed finding has been waived.

| Image / experiment | Observed result | Disposition |
|---|---|---|
| Current CI final backend | 75 HIGH + 1 CRITICAL; 33 IDs; no listed fixed versions | Release blocked; exact current package inventory below |
| Current CI final frontend | 0 HIGH/CRITICAL | This scope passed; not a backend/build-stage clearance |
| Bookworm full application candidate | 100 HIGH + 4 CRITICAL; 50 IDs; no listed fixes | Rejected: worsens findings. Only both FROM lines changed in temporary Dockerfile; no production pin change |
| Alpine candidate | Python 3.14.8 starts; top-level `--no-deps` binary-wheel probe passed. Full build failed fetching APK indexes with DNS errors | Unqualified; no final-image scan or transitive/PDF/OCR compatibility claim. Package-resolution errors after failed indexes do not prove packages unavailable |
| Earlier build-stage assessment | 17 HIGH rows: 4 bundled npm, 13 native TypeScript compiler Go dependencies | Historical 16 October evidence; not rescanned here. Separate from final frontend and clean declared npm graph |

CI and local scans are separate executions; database freshness can differ. Counts
are observed results, not a controlled equal-database delta. Bookworm still fails
the unchanged gate and is not a remediation candidate.

Each current backend advisory remains **open / review required**. All fixed-version
fields are empty. The JSON includes installed versions and vendor status per row;
the table groups packages by advisory to keep all 33 IDs visible.

| Advisory | Severity | Installed package(s) |
|---|---|---|
| [CVE-2026-6653](https://avd.aquasec.com/nvd/cve-2026-6653) | CRITICAL | libxml2 |
| [CVE-2025-69720](https://avd.aquasec.com/nvd/cve-2025-69720) | HIGH | libncursesw6, libtinfo6, ncurses-base, ncurses-bin |
| [CVE-2026-12064](https://avd.aquasec.com/nvd/cve-2026-12064) | HIGH | libcurl4t64 |
| [CVE-2026-16742](https://avd.aquasec.com/nvd/cve-2026-16742) | HIGH | libsystemd0, libudev1 |
| [CVE-2026-36849](https://avd.aquasec.com/nvd/cve-2026-36849) | HIGH | libtiff6 |
| [CVE-2026-52490](https://avd.aquasec.com/nvd/cve-2026-52490) | HIGH | libtiff6 |
| [CVE-2026-54369](https://avd.aquasec.com/nvd/cve-2026-54369) | HIGH | libacl1 |
| [CVE-2026-66046](https://avd.aquasec.com/nvd/cve-2026-66046) | HIGH | libexpat1 |
| [CVE-2026-73066](https://avd.aquasec.com/nvd/cve-2026-73066) | HIGH | libtesseract5, tesseract-ocr |
| [CVE-2026-74860](https://avd.aquasec.com/nvd/cve-2026-74860) | HIGH | libxml2 |
| [CVE-2026-76642](https://avd.aquasec.com/nvd/cve-2026-76642) | HIGH | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux |
| [CVE-2026-76956](https://avd.aquasec.com/nvd/cve-2026-76956) | HIGH | libexpat1 |
| [CVE-2026-76957](https://avd.aquasec.com/nvd/cve-2026-76957) | HIGH | libexpat1 |
| [CVE-2026-77214](https://avd.aquasec.com/nvd/cve-2026-77214) | HIGH | libexpat1 |
| [CVE-2026-78408](https://avd.aquasec.com/nvd/cve-2026-78408) | HIGH | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux |
| [CVE-2026-78409](https://avd.aquasec.com/nvd/cve-2026-78409) | HIGH | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux |
| [CVE-2026-78410](https://avd.aquasec.com/nvd/cve-2026-78410) | HIGH | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux |
| [CVE-2026-8458](https://avd.aquasec.com/nvd/cve-2026-8458) | HIGH | libcurl4t64 |
| [CVE-2026-86138](https://avd.aquasec.com/nvd/cve-2026-86138) | HIGH | libxml2 |
| [CVE-2026-86139](https://avd.aquasec.com/nvd/cve-2026-86139) | HIGH | libxml2 |
| [CVE-2026-86140](https://avd.aquasec.com/nvd/cve-2026-86140) | HIGH | libxml2 |
| [CVE-2026-86142](https://avd.aquasec.com/nvd/cve-2026-86142) | HIGH | libxml2 |
| [CVE-2026-86143](https://avd.aquasec.com/nvd/cve-2026-86143) | HIGH | libxml2 |
| [CVE-2026-86144](https://avd.aquasec.com/nvd/cve-2026-86144) | HIGH | libxml2 |
| [CVE-2026-88047](https://avd.aquasec.com/nvd/cve-2026-88047) | HIGH | libtesseract5, tesseract-ocr |
| [CVE-2026-88048](https://avd.aquasec.com/nvd/cve-2026-88048) | HIGH | libtesseract5, tesseract-ocr |
| [CVE-2026-88051](https://avd.aquasec.com/nvd/cve-2026-88051) | HIGH | libtesseract5, tesseract-ocr |
| [CVE-2026-88052](https://avd.aquasec.com/nvd/cve-2026-88052) | HIGH | libtesseract5, tesseract-ocr |
| [CVE-2026-88053](https://avd.aquasec.com/nvd/cve-2026-88053) | HIGH | libtesseract5, tesseract-ocr |
| [CVE-2026-88806](https://avd.aquasec.com/nvd/cve-2026-88806) | HIGH | libx11-6, libx11-data |
| [CVE-2026-88807](https://avd.aquasec.com/nvd/cve-2026-88807) | HIGH | libxrender1 |
| [CVE-2026-93990](https://avd.aquasec.com/nvd/cve-2026-93990) | HIGH | libexpat1 |
| [CVE-2026-9538](https://avd.aquasec.com/nvd/cve-2026-9538) | HIGH | perl-base |

## Decision and older-finding index — nothing silently dropped

[DECISIONS-NEEDED.md](../DECISIONS-NEEDED.md) retains the authoritative options,
existing approvals, interim controls and owner inputs. This index carries forward
all numbered sections, including older defects and workflow dead ends. Its older
source/configuration descriptions are **not newly reproduced findings at this
head**; reconcile current source before promoting one to a confirmed repair.
Resolved portions are not reopened. These are not 32 equally sized engineering tasks.

| Decision section | Finding / boundary carried forward | Handling |
|---|---|---|
| 1 | Enterprise SSO/SCIM/SAML requires a real IdP acceptance run | External configuration and evidence; browser-bound OIDC repair is already implemented |
| 2, 2b | Billing activation, seller-of-record VAT, provider/webhook setup and dogfood activation | Owner/provider acceptance; no paid activation inferred |
| 2a | Conflicting plan ladder | **Resolved 15 August**; historical original remains for context, not a new open task |
| 3 + R002 | DATEV/SAF-T and Xero/QuickBooks require exact localized import contracts/mappings | Generic scoped CSV implemented; provider adapters remain intentionally disabled pending reviewed contracts |
| 4 | Multi-region residency depends on infrastructure and data-plane choice | External decision and deployed isolation proof |
| 5 | Production KEK provider, key lifecycle and recovery | External configuration and real restore proof; local synthetic restore does not certify it |
| 6 | Public API GA acceptance/versioning commitments | Owner acceptance at existing boundary |
| 7 | SOC 2 / ISO certification | External scope/auditor/control evidence; tests do not certify compliance |
| 8 | Decommissioned Fleet Fuel archive retention/destruction | Counsel/owner decision; no deletion or history cleanup inferred |
| 9 | Restating already approved expense figures after FX correction | Preserve approved figures until approved correction policy |
| 10 | Transport pricing | Partly resolved model; retain remaining commercial choices from that section |
| 11 | Unmatched claim-line supplier attribution | Product decision on bucket semantics; inspect current behavior before repair |
| 12 | No pre-send abandonment path for supplier claim-back | Historical workflow gap; choose write-off/abandon semantics before new transition |
| 13 | Drifted frozen claim-back evidence has no approved refresh/reopen route | Fail-closed refusal protects figures; policy needed for recovery path |
| 14 | Excise rate ownership, eligibility and regime deactivation | Advisory placeholders are not statutory entitlement; require verified policy/reference ownership |
| 15 | Report-level expense EUR provenance absent in older assessment | Item-level provenance exists; verify current schema, preserve approved report values and approve legacy provenance treatment |
| 16 | Inbound attachment hard deletion differs from recycle-bin behavior | Confirm current retention path and decide whether extra recoverable storage is wanted; do not invent retention |
| 17 | Anonymous refund-estimate upload not built | Authenticated operation is intentional; owner decision before public exposure |
| 18 | First payment failure suspends access; grace/dunning policy undecided | Billing recovery access repaired previously; do not infer grace days or recipients |
| 19 | Advertised 14-day trial has no clock in older assessment | Historical product/code contradiction; verify current implementation, choose actual trial or correct permanent-tier promise |
| 20 | VPS deploy script, backups/off-host copy, health URL, GHCR promotion, migrations, environment rules and legal documents | Actual deployment settings remain unverified here; record each external outcome, not just repository code |
| 21 | Locale/decimal-input/language policy | Consistent single-locale implementation exists; no browser-locale switch without input review |
| 22 | Private vulnerability reporting channel | Owner supplies and externally tests real contact/reporting route; no invented mailbox |
| 23 | Main branch required checks and protection | Verify actual settings; repository YAML is not proof of protection |
| 24 | Historical identifier classification and empty denylist | V07 preparation complete, named private triage/cleanup still pending; dated counts are not a fresh history scan |
| 25 | Stripe Portal switches, cancellation timing and paused subscription support | Provider dashboard/runbook choices; duplicate checkout and silent free switch guards already repaired |
| 26 | Shared supplier bank account show/warn/refuse semantics | Collision display exists; refusing legitimate shared accounts is a product decision; no cross-tenant matching inferred |
| 27 | Workspace closure confirmation, legal hold, retained records and billing | Export half implemented; destructive closure policy/build remains separate |
| 28 | Hosted dependency review unavailable in earlier CI | V06a replacement checks now pass. Human control review remains; hosted service is optional, not an unresolved package defect |
| 29 | Final native and build-image advisory dispositions | V05b first in the queue above; no waiver approved |
| 30 | Historical SMTP acceptance and partial action uncertainty | Operator evidence and explicit recovery; durable future automation is implemented |
| 31 | Paid external extraction provider, region, terms, retention, budget, credentials and quality corpus | Local deterministic/OCR capture exists; external selection/acceptance pending |
| 32 | Pre-fix credit audit events may lack semantic document target | New issuance fixed; historical reconciliation remains, without hash-chain rewriting |
| Retention reconciliation (unnumbered) | Invoice recycle/archive chain and ex-client retention | Existing decisions stay approved; legal wording and remaining entity-specific boundaries are not silently changed |
| Project lifecycle (unnumbered) | Reviewed legal templates; adjustable final invoices; client-defined offer numbering | Owner choices already answered; reviewed template text/jurisdiction is still external delivery, not a reopened design decision |

The broader business/personal vision is **product scope, not a completed backlog**:
private/household/business spaces, real account balances, transfers versus purchases,
card settlement, forecasting and connected evidence need their own scoped acceptance.
A received-invoice budget is not complete household finances; receivables minus
payables is not bank cash. See [product requirements](../product/product-requirements.md)
and the [report reconciliation](RECONCILIATION-2026-10-16.md). Each future action
needs an outcome, visible refusal/recovery and a specific development prompt before
implementation; do not promise the whole vision from completed repair rows.

## Implemented findings — preserve evidence, do not repair twice

[Backlog](../BACKLOG.md), [combined verification](FINAL-VERIFICATION-2026-10-16.md)
and [uploaded-review reconciliation](RECONCILIATION-2026-10-16.md) keep the full
per-item prompts and evidence, including earlier review IDs. Completed code is
separate from human review, production acceptance and historical remediation.

| Findings | Implemented outcome / evidence entry point |
|---|---|
| V01a/V01b | Patched pypdf and killable bounded untrusted-PDF parsing; [security plan](../security/VULNERABILITY-ANALYSIS-AND-FIX-PLAN-2026-10-16.md) |
| V02/V03 | PyJWT replacement and required OIDC claims; declared Python audit clean on baseline CI |
| V04/V05a/V06a/V07a | Frontend lock repair, clean final frontend origin, supported dependency review and private triage preparation; residual native images/history/control acceptance remain above |
| ECC tenant/access/cache | Session-specific workspace, stale-tab refusal, one-use browser-bound OIDC, offboarding/calendar and account-cache isolation; [repair](../plan/ECC-SECURITY-2026-10-15.md), [cache](../plan/ECC-IDENTITY-CACHE-2026-10-15.md) |
| R011/R012/R016 | Invoice issue/delivery, cash application/corrections and credit commands; [issue](../plan/R011-INVOICE-COMMANDS-2026-10-13.md), [cash](../plan/R012-CASH-COMMANDS-2026-10-13.md), [credit](../plan/R016-CREDIT-COMMANDS-2026-10-16.md). Historical truth limits remain above |
| R002/E16-03/reconciliation currency | Scoped generic accounting preview/download, EUR projection with FX provenance/incomplete states, source-currency display; preserved snapshots and reviewed-provider boundaries |
| R014/R015 | Complete-or-refused bounded PDF ZIP with manifest/hashes, durable attachment commands and visible download failure/recovery |
| FP-P01, R008-A-AU04, FS16-01/02/03 | Immutable published automation, validation/revision conflicts and lossless condition editing |
| R008-A-AU06, FS16-04, FP-P02/FS16-05 | Truthful side-effect-free preview and durable version-pinned automation/mail children with uncertainty preserved |
| N1/N4/L2 | Supplier identifiers and masked IBAN document snapshots, captured/calculated line amounts, bounded page previews and received-invoice VAT breakdown |
| E16-05 | Bounded synthetic finance/restore pilot; not hardware minimum, legal/accountant or production certification |
| R017–R019 | Bounded capture reads/storage refusal, atomic registry/run/job/usage admission and PostgreSQL quota locking; source merged through R019 |
| R020/R021 | Post-wait capture access refresh, original/page read authorization and hash/size truth; draft PR #64. [R020](../plan/R020-CAPTURE-ACCESS-REFRESH-2026-10-17.md), [R021](../plan/R021-CAPTURE-DOWNLOAD-TRUTH-2026-10-17.md) |
| X1–X4 and earlier shipped items | Batch upload, progress, encrypted SSO secrets and CI visual checks; retained in backlog history |

R021 local evidence: **3,675 backend passed / 71 skipped**, plus **88 explicit
PostgreSQL checks** passed; real synthetic source/page downloads matched hashes.
Current baseline CI completed successfully for the application checks, while the
image gate failed. New documentation commits need their own CI status; previous
results identify the application baseline rather than claiming a future head passed.

## Boundaries and tracking rules

- The earlier approximately **93%** figure counted implemented rows in a small
  engineering table. Rows differ greatly in size; it is not percent effort, release
  readiness or the business/personal vision. Do not use it as a release estimate.
- R020/R021 refresh access at a documented point after I/O; they do not serialize
  every future revocation against commit/delivery or recall already received bytes.
  This is an explicit design boundary, not a newly reproduced bypass.
- Keep historical evidence dated: V07 private inventory and the build-stage scan
  were not rerun here. Rejected Bookworm and unqualified Alpine are experiments,
  not production repairs. No final-image finding is cleared by a clean pip/npm audit.
- A queue item needs status, evidence, next action and its acceptance checks. Log
  newly reproduced defects here before adding their implementation row to the
  backlog; retain original IDs and link fixes. Avoid duplicating owner decisions.
- No source, customer data, audit history, security exclusions, deployment or main
  merge changed in this documentation slice. Human/lead review remains required.
