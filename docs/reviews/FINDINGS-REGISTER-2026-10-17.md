# Known findings register — 17 October 2026

## EX18 create-command delivery — 27 October 2026

[Implementation, action-specific maintenance prompts and qualification](../plan/REIMBURSEMENT-CREATE-COMMAND-2026-10-27.md).
The candidate on PR #85's fully qualified head uses ordered report locks,
observed versions and the shared actor/tenant/key receipt. Local qualification:
79 API, 18 PG, 54 focused browser, plus actual 102-report cross-page creation,
exact EUR 0.30 readback and no-write replay/conflict. All 872 functional browser
cases pass without retries/skips/flakes. Fresh hosted status is tracked in the linked receipt; human/Lead review remains required.

| Finding | Current bounded state | Remaining boundary |
| --- | --- | --- |
| O05-RB01 / EX18 overlap and uncertain create | Locked exact selection, atomic receipt/audit/keyed intent, authorized lookup, original-key replay and UI recovery locally qualified; removing the real report lock makes its regression fail | Fresh full hosted qualification; no-key legacy clients lack outcome recovery; session/module control semantics remain explicit |
| EX18 eligible/read population | Approved and marked unbatched rows paged with versions; exact EUR total and incomplete FX; grouped batch counts; legacy implicit partial result refused | Soft batch FK/migration and historical anomalies remain unqualified; no automatic repair |
| EX18 receipt authority | New lookup and both older expense aliases require current approver permission for reimbursement receipts | No permission expansion; wider report-cycle receipt work remains open |
| O05-UI01 reimbursement body | Document/body390px at390px/root32; all facts retained with native local table scrolling; fresh page shrink verified | Other device/320px combinations, shared shell, screen-reader/WCAG qualification remain open |
| EX19 / EX20 / EX21 / EX22 | Minimum exported-cancellation guard is qualified separately; creation does not settle or send money | Full cancellation recovery, payment evidence/recovery and artifact contracts remain ordered work |

PR #85 hosted CI is now complete at `262d4b4`: 4,542 backend, 208 PostgreSQL,
854 functional, 13 ordinary visual and ten clean image targets with twenty
exported hashes verified. Its pending notes below are historical; no main merge,
deployment or release-control closure is inferred.

## EX19 minimum guard delivery — 27 October 2026

[Scoped implementation, controls, read-only preflight and remaining prompts](../security/REIMBURSEMENT-CANCEL-GUARD-2026-10-27.md).
The original CSV/SEPA export-first API and PG controls fail on the old 204;
the candidate returns typed 409 with unchanged financial/audit/outbox state.
Actual unmocked browser/API/PG acceptance and 854 functional browser cases pass.
Fresh hosted qualification and human/Lead review remain required; no deployment.

| Finding | Current bounded state | Remaining work |
| --- | --- | --- |
| O05-RB02 / EX19 exported cancellation | Three-marker refusal and explained UI locally qualified, including actual locked HTTP route schedules | Full reason/version/key/receipt/status recovery, financial write transport/navigation outcomes and legacy disposition stay open |
| O05-RB02 legacy evidence | Read-only tenant preflight executed on synthetic fixtures; missing/invalid org and writes refused | Zero markers cannot exclude historical exports; no production audit or automatic repair claimed |
| O05-UI01 reimbursement page enlarged text | Changed payout card fits at390px/root32; inherited page width497 remains versus927 baseline | Separate full-page header/approved-list reflow prompt in delivery record; no complete accessibility claim |

## Daily-report reconciliation — 27 October 2026

[Source findings, completed candidate CI and ordered prompts](DAILY-REPORT-RECONCILIATION-2026-10-27.md).
Main remains `90891d8d`; both candidate PRs #82/#83 now pass eligible CI/supply
jobs. Earlier pending/failed receipts remain historical. The attachment's final
27 October entry supersedes its opening summary; no attached prompt was executed.

| Finding / action | Evidence classification | Status / next work |
| --- | --- | --- |
| O05-RB02 / EX19 exported cancellation | Source-confirmed open-state check permits export-first cancellation and releases links; existing batch locks do not prevent it | Open, first bounded finance repair. Add no-write export guard/help and legacy preflight; complete version/reason/receipt/recovery separately. No production duplicate payment is claimed. |
| O05-RB01 / EX18 batch creation | Ordinary selected-report read lacks locks/versions/key; duplicate IDs distort metadata counts, not money totals; first 100 approved rows are not necessarily eligible | Open. Exact unique selection, deterministic locks, recoverable receipt/status and bounded eligible/batch reads, with actual route/PG races. |
| O05-RB03 / EX20 recorded payment | Existing maker-checker, partial evidence and batch GET are present; command recovery/provider basis are missing; nullable reference is intentional | Open. Clarify internal assertion and per-batch recovery. Required evidence fields follow decision 33 rather than an invented rule. |
| EX16 / EX17 continuation | Marked reports disappear from the approved-only selector; direct terminal action bypasses batch evidence/maker-checker despite report serialization | Open separate action contracts; retain shared receipt/cycle prerequisites and independent release-blocker status. |
| B00/F10/readiness | Fresh metadata: no effective main rules/protection; current failed history scan has 872 unresolved scanner rows, not confirmed PII; current health URL unavailable to this integration | Open external controls. Classify occurrences, verify authorized branch policy and served revision; no settings, denylist or history change here. |

The report's 81 semantic records are not 81 verified journeys. New EX18–20 remain
zero of three newly runtime-verified. Existing EX09/manual recovery is implemented
on main; do not reassign historical findings without a new regression. Keep the
separate CTX UI queue and all prior evidence below.

## Selected-region OCR findings — 21 October 2026

The user-approved recovery slice is locally qualified in the candidate based
on OCR/navigation PR #75 at `0bbeb26e`. See [delivery record and complete action
prompts](OCR-REGION-REVIEW-2026-10-21.md); hosted qualification and the customer's
actual invoice remain separate.

| Finding | Evidence classification | Status / next work |
| --- | --- | --- |
| OCR-MEMORY-01 cross-capture correction race | Actual nonsuperuser PostgreSQL concurrency reproduced `uq_capture_field_memory_key` when two captures update the same supplier/field | Repaired by atomic upsert/increment. Final 24-case PG cohort includes this regression. Bank instructions remain excluded. |
| OCR-CONFIRM-01 unversioned final create | Failure-first stale review/confirm controls and actual second-tab browser/API refusal | Repaired for region-aware clients: loaded base plus canonical draft checked under capture lock; exact save receipt advances UI base. After region apply a missing base is refused. Legacy captures without apply retain staged optional compatibility. |
| OCR-CONFIRM-02 authority changes during lock wait | Actual blocked confirm returned 201 after offboarding and after same-session organization switch | Repaired by post-lock identity/policy refresh in direct and inbound capture confirms. Both actual PostgreSQL regressions pass before financial writes. |
| OCR-LOCK-01 admission/capture lock cycle | Three PostgreSQL failure-first controls reproduced deadlocks against append, manual review and worker publication | Repaired by admission `FOR NO KEY UPDATE`; all three controls pass without removing serialized admission. |
| Go CVE-2026-111943 / GO-2026-6809 | Current scanner feed reports inherited Go 1.27.2 build/runtime binaries | Go 1.27.3 digest pins and qualified TypeScript compiler rebuild applied. Parallel candidate's four affected images scan clean; this OCR head requires hosted image qualification. |
| OCR-TABLE-01 initial whole-document row quality | Actual native synthetic vertical-table baseline produced two spurious rows | Open. No invoice created from this fixture. Preserve fixture and quantify row/column correctness; the separately qualified 90-degree region append does not fix baseline parsing. |
| Customer invoice accuracy / Hostinger capacity | Affected invoice is unavailable; bounded synthetic production-image measurements are not VPS capacity evidence | Open. English-only journeys do not qualify Latvian recognition, actual customer extraction or production sizing. |

**Current development — manual expense draft completion, 20 October 2026:**
EX09 and EX05-MANUAL are implemented in the isolated candidate based on main
`e5b101756c83f1668925e44e667bc0ae72f564d4`. The repair adds exact cent amounts,
one frozen save intention and a validated, scoped outcome lookup. Known saves
stay distinct from failures to load their current report; retries use the original
key and inputs only while that frozen intention remains available in memory.
[Implementation and qualification](../security/R010-MANUAL-DRAFT-2026-10-20.md); [action contracts](../plan/R010-MANUAL-DRAFT-2026-10-20.md).
The bounded manual slice is locally qualified: 44 helper, 43 mocked UI and
three fresh actual browser/API/PostgreSQL cases pass. Fresh hosted full
qualification is pending; local full-suite/visual success is not claimed.
No backend product source, schema, dependency, monetary rounding contract or
visual baseline is changed. The complete R010 parent and broader backlog remain open.

**Completed preceding CI batch:** [PR #70](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/pull/70)
merged normally into main `e5b10175`, including dependency PRs #68/#69.
[Fresh main CI](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39439209083)
passed all seven required jobs, both eligible supply-chain jobs and all four
release jobs; the deployment job passed. Backend: 4,421 passed / 156 skipped;
browser: 620 + 13 unchanged visual checks. Ten image targets had zero
HIGH/CRITICAL rows; all 20 report hashes and six native storage groups were
verified. These results qualify that preceding main, not the new manual repair.
The deployment public-health URL is unset; live entrypoint, served revision and
public readiness remain unverified. Historical PII classification, SSH trust,
qualified deployment revision/cancellation, provider/cutover and the original
working copy's separate UI/auth/directory/AP edits remain open or excluded.

## Current bounded repair findings — 20 October 2026

| Finding / action | Evidence classification | Status / next evidence |
| --- | --- | --- |
| WF-14 / EX09 manual preview and unkeyed save | Actual synthetic-data browser/API/PG baseline: two `0.005` lines showed 0.01 but stored 0.02; explicit request key absent. Independent counts were 1 report / 2 items / 1 command / 1 targeted audit. | Client exact-cent validation and one frozen save intention locally qualified; 44 helper / 43 UI / 3 actual manual cases pass. Fresh hosted full qualification pending. |
| WF-14 / EX05-MANUAL uncertain outcome | Source-confirmed absence of a manual typed lookup, plus helper failure-first cases accepting invalid/mismatched responses before metadata clear. | Validated lookup and known-ID readback recovery locally qualified, including actual two-GET response-loss recovery with unchanged PG counts. Fresh hosted full qualification pending. |
| Manual/shared command stale async dispatch | Synthetic helper reproduction sends an old-scope request with the later token after held hashing, or a recovery GET after canceled old-session response. Real server expected-identity fences would refuse; no cross-company write is inferred. | Generation/token guard locally qualified; 44 final helper and 12 existing invoice/cash browser cases pass. Fresh hosted qualification pending. |
| Manual form navigation lifetime | Synthetic browser diagnostic: hold first digest, navigate away/back, complete a later save, release old digest; obsolete form sends another independent key/title under the same identity. No second actual PG commit is claimed. | Form-lifetime controller locally qualified by final helper/UI cohorts: obsolete pre-dispatch work refuses, while after-dispatch evidence is retained. Cancel does not imply server rollback; fresh hosted qualification pending. |
| Manual status lookup navigation lifetime | Anchored synthetic regression: hold typed receipt GET, navigate through actual unmount/back, release valid response; old helper clears manual reference while current form has no saved ID. | Separate lookup controller and optional helper signal preserve evidence across unmount; final UI and independent abort control pass. No server rollback/mutation is inferred; hosted qualification pending. |

The [implementation report](../security/R010-MANUAL-DRAFT-2026-10-20.md)
classifies the repair, initial fixture failures and qualification boundaries.
These findings do not make the complete R010 parent or broader backlog finished.

## Prior publication-time snapshots

**Current follow-up candidate — 20 October 2026:** configurable database
backups and independent Playwright job-pin validation are implemented and
locally qualified. The isolated candidate also includes qualified dependency
PRs #68/#69; their separate earlier checks do not qualify this combined head.
[Backup identity, real PostgreSQL restore evidence and limits](../security/CI-CUSTOM-DB-BACKUP-2026-10-20.md);
[own-job validation, failure-first controls and scope](../security/CI-PLAYWRIGHT-OWN-PIN-2026-10-20.md). The new candidate
collects 4,577 backend cases; 118 focused checks pass. Full hosted qualification
remains pending. Existing main `d772a366` passed all seven required CI jobs,
release, eligible supply-chain and deployment jobs; public readiness and the
live entrypoint/served revision remain unverified. Historical PII classification,
trusted SSH identity, exact deployment revision/cancellation and other workflow
items remain open. The original workspace's uncommitted UI/auth/directory/AP
changes are not part of this isolated candidate.

## Earlier review and qualification snapshots

**20 October CI-20-VPS-QUIESCE:** source review found that the Hostinger deploy
script did not enforce the capture migrations' writer-stop preflight. The
[repair](../security/CI-VPS-WRITER-QUIESCE-2026-10-20.md) builds before maintenance,
verifies all writer containers stopped, takes quiesced backups and refuses
automatic old-consumer restart after startup begins. Twenty Bash regressions
pass; new-head hosted qualification/main integration remain pending. S3 provider
selection is separate from Hostinger's local-storage deployment. Earlier green
`22c9b3af` evidence remains scoped to that pre-rollout-repair head.

**19 October completed CI baseline:** `0d81c0f7d244c911d739a25db2ef49a6ca9f50c2`
passed all seven required jobs in [CI 39256160223](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39256160223)
and [supply-chain 39256160225](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39256160225):
**4,248 backend passes / 122 existing skips**, **140 PostgreSQL checks plus six
growth checks**, and **620 browser / 13 visual passes**.
The [nine rebuilt outputs](../security/CI-IMAGE-REPAIR-2026-10-19.md) and
[RustFS development candidate](../security/CI-STORAGE-REPLACEMENT-2026-10-19.md)
give **ten images with zero HIGH/CRITICAL findings**. All six native storage
groups passed with cleanup confirmed; all 20 SBOM/audit hashes were verified.
This baseline includes the local/S3 repairs in rows 7w/7x. Export redemption and
artifact-integrity repairs in 7t/7y are implemented and locally qualified; their
exact-head CI remains pending.
Human review remains open. The native adapter checks prove real
403/client-loopback refusal and recovery, not stopped-provider or midstream
fault qualification. Production starts still refuse pending provider/cutover approval.
Earlier 47-row snapshots below retain their dated scope. Host CI's original
TypeScript executable remains the separate CI-HOST-TSC exposure.

**Repair update:** [SYS-01 OIDC destination/issuer repair](../security/SYS-01-OIDC-DESTINATIONS-2026-10-17.md)
is implemented with local evidence, pending exact-head CI/human review and external
IdP acceptance. [SYS-02 Compose readiness](../security/SYS-02-COMPOSE-READINESS-2026-10-17.md)
is also implemented with native failure/recovery evidence, pending CI/human review.
[SYS-03 fresh-volume permissions](../security/SYS-03-STORAGE-VOLUME-2026-10-17.md)
is implemented with native API/worker/refusal/restart evidence, pending CI/human review.
[SYS-04 runtime repair and expanded audit](../security/SYS-04-IMAGE-COVERAGE-2026-10-17.md)
is implemented, pending new-head CI/review. Its dated 17 October inventory had
both final application images at **zero HIGH/CRITICAL**, backend builder 4 HIGH,
frontend builder 18 HIGH, PostgreSQL 24 HIGH + 1 CRITICAL and three unavailable
service references blocking that earlier gate. All 47 historical package/advisory
rows are preserved; the later ten-image pass is recorded above. **18 October update:**
[CAP-RETRY](../plan/CAP-RETRY-ATTEMPT-FENCING-2026-10-18.md) is implemented with
PostgreSQL/native-worker evidence, pending exact-head CI/human review.
[CAP-COMMAND](../plan/CAP-COMMAND-DURABLE-ADMISSION-2026-10-18.md) is also implemented
with per-file command receipts and native browser/API/worker recovery evidence,
pending exact-head CI/human review. [CAP-RESOURCE handler repair](../security/CAP-RESOURCE-HANDLER-BOUNDS-2026-10-18.md)
is implemented with eleven bounded upload reads and native refusal/recovery
measurements. [CAP-INGRESS API controls](../security/CAP-INGRESS-BOUNDARIES-2026-10-18.md)
are implemented with pre-parser boundaries, backpressure, byte-aware browser
packing and native rolled-spool/recovery evidence. [SYS-10 development proxy repair](../security/SYS-10-DEV-UPLOAD-PROXY-2026-10-18.md)
is implemented, pending exact-head CI/human review: known refusal truth, separate
upload connections, explicit uncertainty and native same-key recovery.
**CAP-CAPACITY ingress repair and CAP-REGISTRY are implemented, pending CI/review:**
body-only deadlines, shared origin admission, streaming and conflict-safe document
metadata with caller-owned transactions. [Four-worker/native decode, 128-case PG
and TLS/HTTP2 evidence](../security/CAP-CAPACITY-INGRESS-2026-10-18.md).
**CAP-DISK-FULL is implemented, pending CI/human review:** bounded native staging/
object exhaustion, cleanup and same-key browser recovery passed; parser capacity
faults now return an explicit 503. [Repair/evidence](../security/CAP-DISK-FULL-2026-10-18.md).
**CAP-OBJECT-READ is implemented, pending CI/human review:** bounded worker reads,
S3 resource closure, visible failures and an ordinary two-worker PDF/OCR profile
passed. [Repair/measurements](../security/CAP-OBJECT-READ-2026-10-18.md).
**CAP-WORKER-SOURCE-HASH is implemented, pending CI/human review:** changed
UI/email bytes are refused before parsing and exact-original restoration succeeds.
[Repair/evidence](../security/CAP-WORKER-SOURCE-HASH-2026-10-18.md).
**CI sweep:** [all 1,039 available runs and current failures](../security/CI-FAILURE-SWEEP-2026-10-18.md)
were inspected directly. Reporting repair is implemented. SQLAlchemy 2.1.4 and
benchmark-startup repairs are implemented with [qualification evidence](../security/CI-DEPS-SQLA21-2026-10-18.md),
pending exact-head CI and Lead review. The other nine backend updates are now
integrated with [Decimal contract and shipped-runtime qualification](../security/CI-DEPS-BACKEND-REST-2026-10-18.md),
pending exact-head CI and Lead approval of the prepared snapshot. Frontend integration
and module-load recovery now have [qualification evidence](../security/CI-DEPS-FRONTEND-2026-10-18.md),
pending exact-head CI/Lead review. Portal data-read failure truth/retry is now
[implemented and locally qualified](../security/UI-PORTAL-READ-TRUTH-2026-10-19.md),
pending new-head CI/Lead review. **Current work:** export redemption and artifact
integrity; remaining CAP-CAPACITY reads/decoding/shared disk/fleet qualification
and Decisions §29 stay open.
The [streamed verification/preview slice](../security/CAP-READ-BOUNDS-2026-10-19.md)
is now implemented with 113 focused / 131 required PG checks and native corruption,
restoration and worker evidence. Fifteen original/download/render/delivery/export
reads remain queued. Known SYS-06 reflection drift is reproduced, with no schema change.
Subsequent head `265b086` CI passed **3,846 backend / 109 skipped**, PostgreSQL,
**595 browser / 13 visual**, frontend/lint/PII; the eight-target image gate still
fails on the recorded build/PG advisories and unavailable services. These are
prior-head results for the new proxy repair; no release clearance is implied.
Prior head `ef2ce23` CI passed PostgreSQL, 593 browser and 13 visual cases;
backend had 3,796 passes/one failure/109 skips. Its nine-file 413-symbol count was
invalid after centralizing ten routes. A replacement checks four response owners,
ten forwarders and seeded deprecated/literal counterexamples; the 93-case
control/boundary sweep passes, with the global deprecated-symbol ban retained.
[Control repair and review boundary](../security/CAP-INGRESS-BOUNDARIES-2026-10-18.md#prior-head-ci-and-canonical-413-control).
Earlier scan/baseline statements
below retain their historical scope; the table carries current implementation status.

**Full-system scan update:** [confirmed defects, complete coverage and repair prompts](../security/FULL-SYSTEM-SCAN-2026-10-17.md)
and [sanitized scan evidence](../security/evidence/FULL-SYSTEM-SCAN-2026-10-17.json).
New OIDC and deployment findings extend the queue; CAP-RETRY is now reproduced.
The earlier image comparison below remains dated evidence, not the full service-image inventory.

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
The initial log-only update changed no application behavior, dependency pin, history
or security gate; the subsequent SYS-01–SYS-04 repairs are linked above.

## Working order — tackle one item at a time

An item closes only with its recorded acceptance evidence. If an owner/vendor
input blocks it, record that boundary and take the next unblocked item. Do not
silently mark a blocked item complete. P1 priorities below are engineering triage,
not CVSS replacements. Existing V/R IDs retain their identity.

| Order | ID / priority | Finding and status | Next action and completion evidence |
|---|---|---|---|
| 1 | SYS-01 / P1 security | **Implemented, pending CI/review:** all server IdP calls require HTTPS and pinned public DNS; metadata issuer is bound to configuration, proxy/redirect bypass refused and provider errors sanitized. | 44 original regressions failed first; 48 focused cases now pass, with related/PG and synthetic TLS evidence. [Repair, compatibility and remaining gates](../security/SYS-01-OIDC-DESTINATIONS-2026-10-17.md). |
| 2 | SYS-02 / P1 deployment | **Implemented, pending CI/review:** base/Hostinger and inherited overlay readiness use Python; database readiness, timings and dependency conditions are preserved. | Six regressions failed first; 45 focused checks, six actual Compose renders and native HTTP/DB failure/recovery/dependency checks passed. [Evidence and rollout limits](../security/SYS-02-COMPOSE-READINESS-2026-10-17.md). |
| 3 | SYS-03 / P1 storage | **Implemented, pending CI/review:** fresh named volumes copy the image's UID/GID 10001, mode 0700; nonempty existing ownership is preserved. | Original failure and exclusion counterexample reproduced; 11 image-volume stages and real API/native-worker/refusal/restart checks passed. [Evidence/operator limits](../security/SYS-03-STORAGE-VOLUME-2026-10-17.md). Incompatible legacy volumes need a reviewed migration. |
| 4 | V05b + SYS-04 / P1 release blocker | **0d81c0f passed seven required CI jobs and the ten-image gate.** RustFS replaces the unavailable images with legacy-data refusal; six baseline native storage groups passed with cleanup confirmed. Both production bootstrap actions still refuse pending approved provider/cutover. | [Storage candidate and qualification](../security/CI-STORAGE-REPLACEMENT-2026-10-19.md); [owner selection](../DECISIONS-NEEDED.md#29-residual-nativebuild-image-findings-v05b-2026-10-16). The subsequent export work needs exact-head CI; Lead/provider review remain pending. Earlier [47 rows](../security/evidence/SYS-04-IMAGE-COVERAGE-2026-10-17.json) are preserved. No gate waiver. |
| 4a | CI-HOST-TSC / P1 known tooling exposure | Host npm installs retain official TypeScript7.0.2's Go1.26.4 executable. The qualified Docker rebuild does not replace it in host lint/build jobs. | Consume a shared verified rebuilt compiler artifact before host execution; preserve source/version/checksum/license, diagnostic, browser and bundle controls. [Scope and prompt](../security/CI-IMAGE-REPAIR-2026-10-19.md#remaining-decision-and-development-prompts). Not an observed failing host check. |
| 5 | CAP-RETRY / P1 confirmed defect | **Implemented, pending CI/review:** generation-bound jobs, atomic draft/pages/provenance/observation publication, fenced progress and serialized review/reset prevent the reproduced 17→34 corruption. | [PostgreSQL/native evidence, rollout and remaining limits](../plan/CAP-RETRY-ATTEMPT-FENCING-2026-10-18.md). Original reproduction retained; current repair does not approve release. |
| 6 | CAP-COMMAND / P1 follow-up | **Implemented, pending CI/review:** per-file receipt, payload-bound replay/status lookup, purge-safe retirement and account/workspace browser recovery prevent duplicate admission/job/usage after uncertain responses. | [PostgreSQL/native browser evidence, migration and limits](../plan/CAP-COMMAND-DURABLE-ADMISSION-2026-10-18.md). Legacy keyless callers and historical captures have no invented command identity. |
| 7 | CAP-RESOURCE / P2 handler repair | **Handler repair implemented, pending CI/review; broader resource closure open:** eleven reads now honor purpose caps before processing and close handles on all outcomes. | [44 baseline failures, 159 boundary/parity + 177 related checks and native measurements](../security/CAP-RESOURCE-HANDLER-BOUNDS-2026-10-18.md). CAP-INGRESS implements API pre-parser bounds; CAP-CAPACITY retains aggregate qualification. Do not mark global resource use bounded. |
| 7a | CAP-INGRESS / P2 API repair | **Implemented, pending CI/review:** pre-parser wire/header/file-count bounds (including empty-boundary framing) cover 13 documented multipart routes plus mailgun/JSON inbound; per-worker two-slot backpressure and browser packing preserve recovery under deployed 20 MiB envelopes. | [Native compiled SPA/NGINX/API/CSV worker, real rolled-spool cleanup and 1 CPU/512 MiB measurements](../security/CAP-INGRESS-BOUNDARIES-2026-10-18.md). Aggregate capacity remains separately open. |
| 7b | SYS-10 / P2 confirmed development-proxy defect | **Implemented, pending CI/review:** scoped Expect handshake, separate upload connections, known response preservation and explicit transport uncertainty. | Four failures reproduced first; 19 actual-Vite protocol/TLS checks and native browser 413→201 / 503→202 same-key recovery with real worker passed. [Repair, limits and review](../security/SYS-10-DEV-UPLOAD-PROXY-2026-10-18.md). |
| 7c | CAP-CAPACITY / P2 aggregate qualification | **Ingress part implemented, pending CI/review; remaining scope is next unblocked.** Body-only idle/absolute deadlines, two shared origin slots and unbuffered HTTP/1.1; measured four-worker/two authorized near-limit JSON profile. Kubernetes `/api` bypasses the frontend limit. | [Native measurements and limits](../security/CAP-CAPACITY-INGRESS-2026-10-18.md). Bounded local disk-full repair/qualification is implemented in CAP-DISK-FULL. CAP-OBJECT-READ bounds both worker source reads and qualifies an ordinary two-worker PDF/OCR profile. Next: remaining API/download reads, worst-case PDF/OCR, shared disk pressure, fleet/controller admission and headroom. No budget rises, automatic retention/GC or global resource-closure claim. |
| 7d | CAP-REGISTRY / P1 confirmed concurrency defect | **Implemented, pending CI/review:** concurrent same-content document registration could fail the unique key and return 500. Isolated inserts now reuse the metadata row while preserving caller work; SQLite first-savepoint ownership is retained. | Deterministic PG committed-winner failure reproduced; commit/rollback races and unrelated constraint/outer-rollback controls pass. All 128 required PG checks and real simultaneous authorized delivery passed. [Repair/transaction boundary](../security/CAP-CAPACITY-INGRESS-2026-10-18.md#shared-document-registration--cap-registry). |
| 7e | CAP-EMAIL-REPLAY / P2 source-level verification gap | Same-Message-ID concurrency is unproven: the generic route checks `already_delivered` before writing, with a nonunique org/message index. The capacity run uses different Message-IDs; its metadata repair does not establish delivery replay safety. | Reproduce simultaneous same-message multi-attachment delivery on PG. If needed, introduce a durable tenant/channel/message command boundary that commits intake/jobs together. Preserve distinct deliveries and legacy missing-ID semantics; do not invent historical identities. |
| 7f | CAP-DISK-FULL / P2 confirmed error-contract defect | **Implemented, pending CI/review:** real spool ENOSPC was a misleading parsing 400. Scoped capacity handling returns 503; existing capture storage rollback/outcomes remain. | [6 pre-fix failures, 112 backend / 17 shipped-runtime / 14 browser checks and real ENOSPC recovery](../security/CAP-DISK-FULL-2026-10-18.md). Seven native spools closed; exactly three commands/captures/jobs after recovery. Shared multi-worker pressure, inode/quota/S3 faults and other document writes remain open. |
| 7g | CAP-OBJECT-READ / P2 confirmed resource defects | **Implemented, pending CI/review:** stored worker input was unbounded and S3 bodies remained open. Cap+1 reads, closure and durable `stored_file_too_large` worklist outcomes now preserve originals and worker recovery. | [4 pre-fix failures; 122 backend / 129 required PG / 27 shipped-runtime checks; native concurrent PDF/OCR](../security/CAP-OBJECT-READ-2026-10-18.md). Two workers peaked at 428.7 / 396.9 MiB under 1 GiB each. Other reads, worst-case decoding and fleet/shared-disk headroom remain open; cap-control correction needs Lead review. |
| 7h | CAP-WORKER-SOURCE-HASH / P1 confirmed integrity defect | **Implemented, pending CI/review:** changed valid sources could publish 999 instead of 100 under the original hash. Workers now verify bounded bytes before parsing and publish an explained mismatch failure without claiming an intact original. | [Two pre-fix failures, 138 backend / 130 required PG / 29 shipped-runtime / 9 browser checks and native exact-original restoration](../security/CAP-WORKER-SOURCE-HASH-2026-10-18.md). Original references/commands remain; unrelated object reads and email-state concurrency are separate work. |
| 7i | CAP-EMAIL-STATE / P2 source-level concurrency gap | Email worker success/failure and confirmation read status before later writes without a shared attempt/publication protocol. Current source-hash verification does not establish duplicate-worker/confirmation safety. | Reproduce overlapping extraction/read failure with pending/confirmed/discarded states on PG. Choose conditional publication and review/confirmation locking from demonstrated interleavings; preserve confirmed invoices, retained evidence and audit truth. Do not claim a new email retry workflow exists. |
| 7j | CI-DEPS-SQLA21 / P2 confirmed candidate incompatibility | **Implemented, pending CI/Lead review:** isolated 2.1.4 reproduced five runtime/control failures and 118 mypy errors on the repaired base. Direct result APIs and precise types now pass; the tenant control requires the new nested predicate, backed by a cross-tenant row regression that fails on 2.0.52. | [33 focused / 131 required PG checks, shipped-runtime and paired growth evidence](../security/CI-DEPS-SQLA21-2026-10-18.md). No warning, type or RLS exemption. Remaining updates are separately qualified in 7l; no dependency PR is merged. |
| 7k | PERF-HARNESS-STARTUP / P2 confirmed measurement defect | **Implemented, pending CI/Lead review:** 375 objects frozen by the embedding runtime caused the harness to skip application heap freezing/GC initialization. Both ORM versions breached the unchanged 8× growth limit; four corrected comparisons pass every endpoint limit. | [Two pre-fix failing functional regressions; original and corrected measurements](../security/CI-DEPS-SQLA21-2026-10-18.md). Own successful-startup marker replaces a global-count assumption; initialization stays once before seeding, failure stays retryable, application startup and all budgets are unchanged. |
| 7l | CI-DEPS-BACKEND-REST / P2 candidate contract qualification | **Integrated, pending exact-head CI/Lead review:** nine updates and the generated contract are prepared. The entire OpenAPI diff removes 412 Decimal patterns (410 schema / two parameter locations); 66 sampled Decimal cases and nine actual payment validation/recovery checks preserve prior behavior. | [Semantic diff and qualification](../security/CI-DEPS-BACKEND-REST-2026-10-18.md): 125 focused / 131 PG / 76 shipped checks, old-to-new image replay on the same PG database, native capture restoration and four-worker ingestion. Server controls and budgets are unchanged. The Lead must review the generated baseline; the full image/history/provider gates remain open. |
| 7m | CI-DEPS-FRONTEND / P2 candidate integration | **Implemented, pending CI/Lead review:** exact PR #61 lock integrated; both browser images match Playwright 1.63. The eager-shell candidate failed at **464.3/136.0 kB**; deferring the protected shell now passes at **434.3/128.2**, below unchanged **460/135**. | [603 broad / 22 final focused browser checks, 19 proxy / 17 governance checks, native compiled-origin recovery and clean final-image/npm audits](../security/CI-DEPS-FRONTEND-2026-10-18.md). The broad run predates only the final public boundary/seed-once fixture tightening; focused/native checks cover those changes. Exact-head CI required; no dependency branch merge or self-approved visual baseline. |
| 7n | TEST-GC-ISOLATION / P3 confirmed test-state leakage — later | The existing real-heap freeze test does not restore its prior process GC state when some objects were already frozen. An isolated app-import probe passes the test but changes **375→359,816** frozen objects and thresholds **(700,10,10)→(700,10,100)**. This is test-process behavior, not a production mutation. | Move real GC/heap assertions into an isolated subprocess, retaining a positive proof of live-object freezing and normal startup ordering. Prove the parent collector state stays unchanged; keep the new harness once/retry regressions. Fresh-process growth/native measurements remain separate evidence. [Probe and qualification](../security/CI-DEPS-SQLA21-2026-10-18.md). |
| 7o | CI-LAZY-PAGE-RETRY and CI-PUBLIC-MODULE-RECOVERY / P2 confirmed UI dead ends | **Implemented, pending CI/Lead review:** React caches a rejected page import, so the former Try again remount never refetched it. A public portal import failure also blanked the page. Both are reproduced before repair. | [Typed load-error marker, explicit protected/public reload recovery and five final browser/native flows](../security/CI-DEPS-FRONTEND-2026-10-18.md). Sign-in stays independent of the protected shell; ordinary render-error remounts and account/workspace boundaries remain. |
| 7p | UI-PORTAL-READ-TRUTH / P2 confirmed UI defect | **Implemented, pending new-head CI/Lead review:** valid links affected by transport/5xx failure were falsely labelled invalid and had no recovery. Terminal refusals now retain contact guidance; eligible failures have sanitized errors and finite guarded GET recovery with Retry-After. | [620 browser / 20 focused portal / four backend checks and real committed-read/decision recovery](../security/UI-PORTAL-READ-TRUTH-2026-10-19.md). First-view timestamp preserved, one decision audit/plan, zero invoices/payments. Module recovery in 7o remains separate. |
| 7q | SYS-04-ORIGIN-LIBX11 / P1 final-image finding | **Implemented, pending new-head CI/Lead review:** fresh DB found HIGH CVE-2026-88806 in Alpine origin libx11 1.8.13-r0. Runtime package floor now requires fixed 1.8.14-r0; rebuilt final origin has zero HIGH/CRITICAL rows and passes native workflow. | [Rejected immutable candidate, package/SBOM verification and native proof](../security/UI-PORTAL-READ-TRUTH-2026-10-19.md#fresh-origin-finding-and-package-repair). The complete ten-image gate passed on the cited 0d81c0f baseline; earlier Debian inventory remains dated evidence. The subsequent export head and human review are separate. |
| 7r | CAP-VERIFY-STREAM / P2 confirmed allocation risk | **Implemented, pending new-head CI/Lead review:** integrity verification loaded each entire original before hashing. Fixed 64 KiB reads retain historical-file support, missing/mismatch/error truth and sanitized diagnostics. | [Old-function negative control, 113 focused / 131 PG checks and native 1 GiB corruption/restoration/real worker](../security/CAP-READ-BOUNDS-2026-10-19.md). Native Uvicorn HWM 182.55 MiB, 256 MiB cgroup with cache reclaim and zero OOM. Total I/O/time/metadata/fleet limits remain open. |
| 7s | CAP-PREVIEW-READ / P2 confirmed allocation/error defect | **Implemented, pending new-head CI/Lead review:** a 32 MiB stored page was fully loaded and misreported as missing. The existing 512 KiB read cap now stops at cap+1 and returns safe 503 restoration/original guidance. | [Two baseline failures, local/S3 closure and restoration, exact-limit success, fresh access controls and native proof](../security/CAP-READ-BOUNDS-2026-10-19.md). Small corrupt/missing previews retain existing 404; no original/manifest deletion or new size policy. |
| 7t | CAP-EXPORT-REDEEM / P2 confirmed concurrency defect | **Implemented and locally qualified, exact-head CI/Lead pending:** PostgreSQL review reproduced duplicate HTTP 200 redemption of a one-time export link. Shared `verification.consume` now locks and refreshes tokens, also protecting email verification/password reset. Locked export rows serialize redemption of one ready artifact. | [Repair and qualification](../security/EXPORT-REDEMPTION-AND-INTEGRITY-2026-10-19.md): **30 new PG cases** (12 export HTTP / 18 shared auth); **170 required PG passed in 78.83 s plus six growth checks in 11.97 s**. Multi-archive `LIMIT 1` passes; the exact no-limit negative control fails its row-lock assertion as intended. Cached identity, post-wait expiry and caller-owned commit/rollback are covered. Production rejects SQLite; development SQLite concurrency remains unproven. Archive newest-ready selection remains without binding migration; purge races and post-commit lost-response policy remain separate. |
| 7u | CAP-ISSUED-ATTACHMENT-READ / P2 confirmed allocation risk | **Implemented, pending new-head CI/Lead review:** a 32 MiB replacement was fully loaded before size/SHA refusal. Download now reads at most admitted size plus one (two bytes for legacy zero), preserving historical objects and safe restoration guidance. | [Local/S3 finite-read regression, historical/legacy compatibility, PostgreSQL access changes and runtime checks](../security/CAP-ISSUED-ATTACHMENT-READ-2026-10-19.md). Fifteen uncapped candidates remain; admitted payload buffering and aggregate concurrency are still capacity work. |
| 7v | CAP-LOCAL-READ-ERROR / P2 confirmed error-contract defect | **Implemented, pending new-head CI/Lead review:** seven HTTP regressions returned generic errors; 27 adapter checks failed. Local open/read/close faults now give sanitized 503 retry guidance, preserve true missing responses and primary failures, and refuse partial downloads/exports. | [Actual-file faults, export worker/token/audit recovery, PostgreSQL and shipped-runtime checks](../security/CAP-LOCAL-READ-ERROR-2026-10-19.md). Full admitted buffers and 15 uncapped reads remain; the local digest repair is in 7w and S3 remains separate in 7x. |
| 7w | CAP-DIGEST-ERROR-CLASS / P2 confirmed error-contract defect | **Implemented with CI passed, pending Lead review:** actual-file post-open missing and simultaneous read/cleanup failures are reproduced. Local digest now reserves missing for lookup, sanitizes post-open filesystem errors, and preserves primary exceptions/cancellation through secondary filesystem cleanup errors. Fixed 64 KiB hashing retains historical-file support. | [Repair and evidence](../security/CAP-DIGEST-ERROR-2026-10-19.md): adapter baseline **14 failed / 21 passed**, HTTP baseline **3 failed / 2 passed**; **40 new cases**, **194 focused passed / 80 warnings**. Source closure, continued verification, unchanged originals and restoration are checked. Included in the cited green 0d81c0f CI; S3 is separate in 7x. |
| 7x | CAP-S3-READ-ERROR-CLASS / P2 confirmed error-contract defect | **Implemented with CI passed, pending Lead review:** missing lookup retains its static outcome; provider, malformed-body, read/truncation and close failures become sanitized `StorageReadError`. S3 get/digest preserve primary failures/cancellation through secondary close `OSError`, retaining cap+1 refusal and source closure. Failed exports preserve complete-artifact, token and audit truth. | [Repair and evidence](../security/CAP-S3-READ-ERROR-2026-10-19.md): **55-case adapter baseline: 34 failed / 21 passed**; four denied-export cases failed / two real-missing controls passed; corrected HTTP baseline **4 failed / 4 passed**. **77 new cases**, **260 focused passed / 112 warnings**. Included in green 0d81c0f CI; six native groups passed with cleanup confirmed. Actual 403/client-loopback refusal and read recovery do not qualify stopped-provider or midstream faults. Fifteen uncapped reads and production provider/Lead acceptance remain separate. |
| 7y | CAP-EXPORT-ARTIFACT-INTEGRITY / P2 confirmed error-contract defect | **Implemented and locally qualified, exact-head CI/Lead pending:** PostgreSQL review reproduced HTTP 200 downloads of corrupt stored export artifacts. Redemption now verifies recorded SHA/size before successful download publication; static 503 refusal preserves restoration/retry without partial attachment, audit or committed token consumption. | [Repair and qualification](../security/EXPORT-REDEMPTION-AND-INTEGRITY-2026-10-19.md): **23 new artifact and 265 existing recovery tests passed**, **121 static controls / 22 warnings**, full Ruff check/format **843 files**, mypy **422 sources**, **4,423 collected**. **108 native cases** passed in three fresh 1 CPU / 384 MiB containers; **422 app and 422 fixture hashes verified**, no reported OOM, cleanup confirmed. The initial combined process's **exit 137 remains unexplained**; minimum-hardware acceptance is not claimed. Known-size reads use recorded bounds; unknown sizes remain unbounded with SHA verification. Archive binding, purge races, lost responses and aggregate capacity remain separate. |
| 8 | CAP-STORAGE / P2 verification and policy gap | Atomic local replacement is not power-loss durability or S3 certification; DB rollback can leave an unreferenced shared object. | Inventory references read-only, exercise supported storage fault/restore paths, document durability. Any garbage collection needs an approved retention/reference policy; never delete shared objects in request cleanup. |
| 9 | PILOT-ACCEPT / P1 release evidence gap | Tiny synthetic pilot passed; production KEK restore, deployment readiness, accountant acceptance and minimum hardware remain unproven. | Run approved deployment/restore and realistic workload acceptance with measured CPU/RSS/latency, exact image/source/config and reconciled outputs. Keep customer data out of fixtures. |
| 10 | V07 / P1 private history | **Unresolved historical PII classification:** refreshed all-fetched-ref inventory reproduces 872 masked rows / 196 blobs; current-tree pass cannot clear history. | Named verifier and owner-held hashed denylist, then approved per-value disposition and exact cleanup scope. No identifiers in this register. Decisions §24. |
| 11 | CONTROL-ACCEPT / P1 release governance | Human/lead review, deployment settings and external security controls cannot be certified by passing source tests. | Verify required checks/protection, private disclosure, deployed backup/health/rollback and dependency-control acceptance. Decisions §§20,22,23,28; hosted dependency service is optional after V06a. |
| 12 | HISTORY-RECON / P1 historical truth | Unknown legacy SMTP outcomes and missing pre-fix credit audit targets cannot be inferred or repaired by rewriting history. | Inventory and reconcile relay/legal-document evidence; operator records unresolved cases and approves append-only correction or separate resend. Decisions §§30,32. |
| 13 | PRODUCT-BOUNDARIES / planned work | ERP localization, external extraction, business/personal finance vision and other owner-dependent workflows remain incomplete or unaccepted. | Follow the decision index below and the product plan. Do not count deliberately disabled adapters as successful deliveries or invent commercial/legal policy. |

Additional review items: **SYS-05** records all 32 Kubernetes configuration
candidates (including reviewed false positives); **SYS-06** records PostgreSQL
schema-drift tooling proposing replacements for deliberate column-subset SET NULL
constraints. Both have scoped prompts in the full-system report; neither authorizes
blind hardening or replacing tenant-preserving foreign keys. **SYS-07** records
a SQLite worker-thread/event-loop teardown warning from the passing full suite,
requiring reproduction and cleanup investigation rather than warning suppression.

### Additional boundary found during CAP-RETRY

**CAP-REVIEW-VERSION / P2 protocol gap:** review/save clients submit a run ID but
no generation precondition. The repair serializes review/reset and refuses queued
saves; a client holding an older *completed* draft can still submit after a later
attempt has completed. This is a source-confirmed missing precondition, not a
claim of a reproduced cross-tenant exposure. Define a generation-bound client
review/save contract, propagate it through the UI, and prove stale-tab refusal
and recovery on PostgreSQL/browser paths. Preserve legacy/email compatibility
explicitly; never infer the generation that a caller saw.

**SYS-08 / P2 PostgreSQL rollback defect (18 October):** a full empty-database
`alembic downgrade base` fails in historical revision `01160bebbd7f` when
`sa.DATETIME()` emits PostgreSQL's nonexistent `datetime` type while recreating
`role_policies`. Reproduced from the pre-repair head `0d546afada3c`, so this does
not originate in CAP-RETRY. Upgrade to head and the new capture revision's own
downgrade/upgrade pass; positive capture generations deliberately refuse schema
downgrade. Prompt: reproduce historical PostgreSQL downgrade on a disposable
nonprivileged database, inspect the original role-policy schema and data-loss
semantics, repair dialect portability with reviewed rollback scope, and prove
SQLite/PostgreSQL parity. Do not run historical destructive downgrades on live
tenant data or infer that an empty-schema roundtrip is a production restore.

**SYS-09 / P2 browser repeatability gap (18 October):** the CAP-COMMAND broad
591-case browser run passed 586 cases and timed out on five unrelated setup/read
paths: FE-004 access, dunning and invoice review; delayed prior-account 401 setup;
and bulk-delete undo setup. All five passed unchanged in one-worker isolation
(12.3 s), with no source, assertion, timeout or retry-budget change. Cause remains
unestablished; concurrent native fixture execution is context, not a diagnosis.
Prompt: preserve failure artifacts before rerunning, reproduce under stated
runner/cache/resource conditions, inspect browser console/network/Vite readiness
and fixture isolation, then repair a proven cause without masking real failures.
The subsequent frontend qualification records two additional host failures with
`ERR_NETWORK_CHANGED` on entry assets during Docker lifecycle work, plus a
separate four-browser-worker trial killed by its 2 GiB cgroup. The same limit
with two workers in a stable container namespace passes 603 broad cases; final
focused/native checks cover its later public boundary. These are bounded
mechanisms and retained attempts, not a global diagnosis or closure of SYS-09.
[Traces, resource scope and qualification](../security/CI-DEPS-FRONTEND-2026-10-18.md).
Exact-head hosted browser CI remains required; do not label the original run green. Hosted head `ae6a8a5`
subsequently passed 591 first attempts plus one retry and 13 visual cases. The
retry was a separate capture-command fixture race: its mocked 503 was changed to
404 before the earlier mutation settled. A controlled lookup barrier now checks
disabling/no POST before settlement, retaining the same-key/two-POST assertions;
[correction evidence](../security/CAP-RESOURCE-HANDLER-BOUNDS-2026-10-18.md#prior-head-ci-corrections).
The five earlier timeout causes remain unestablished. CAP-OBJECT-READ later had
one existing single-row-selection timeout (`#sel-run-2`) / seven passes, followed
by 16 traced passes across two unchanged one-worker repetitions. Its initial failure log
and passing traces are archived; the generated failure-context file was inspected
but overwritten by the repeated run. No timeout/retry/assertion was widened and the cause
remains unestablished. See [evidence](../security/CAP-OBJECT-READ-2026-10-18.md).

**SYS-11 / P2 startup verification gap (18 October):** four-worker native startup
reproduced duplicate-key warnings while workers seed the same FX snapshot. Startup
continued and the current seed failure is caught. Missing or incorrect FX coverage
was not demonstrated. Prompt: reproduce simultaneous first boot, compare the final
dated/provenance-bearing row set with a single-worker seed, and make initialization
conflict-safe without changing rates, fabricating fallback completeness or hiding
unrelated database errors. This remains separate from CAP-REGISTRY.

### Repair prompts

These are scoped future tasks, not instructions to execute every change at once.

- **V05b:** Inspect the exact final-image advisory/package inventory and current vendor evidence. Prefer a maintained compatible patched build. Prove PDF generation/rendering, deterministic parsing, OCR, process limits, native jobs, non-root startup and full final-image scanning on the same image. Keep the gate fail-closed; record rejected candidates and request specialist disposition only for individually evidenced applicability.
- **CAP-RETRY:** Extend the now-confirmed real PostgreSQL barrier-based reproduction for concurrent retry requests, duplicate extract jobs and a retry while an earlier worker persists fields. Verify the existing job-claim semantics. Implement an atomic capture attempt claim/generation fence with replay-safe intent; stale workers must not publish draft/pages/provenance/progress or observations. Preserve review corrections unless explicit discard was authorized. Deliver regression and native-worker evidence, including failure/recovery and access changes.
- **CAP-COMMAND:** Design durable per-file admission command identity and authenticated lookup after uncertain responses. Bind payload/workspace identity, quota, registry, run, native job and usage atomically; test lost response, retry, changed payload, batch partial progress and revocation with separate PostgreSQL connections.
- **SYS-10:** Reproduce the actual Vite proxy's early 413/503 and connection-error behavior with a synthetic upstream; retain real upstream status/body when available and explicit recovery when not. Verify upload/download/redirect/cookie/streaming behavior without draining unbounded rejected bodies or fabricating a successful admission.
- **CAP-CAPACITY (remaining CAP-RESOURCE scope):** Measure aggregate proxy/API/spool/object-read and parallel OCR/PDF budgets across the actual worker/replica profile, including authorized near-limit base64, slow-body deadlines and disk-full recovery. Bounded single-worker local ENOSPC and same-key recovery are now [qualified](../security/CAP-DISK-FULL-2026-10-18.md); shared multi-worker pressure and native quota/inode faults remain open. [CAP-OBJECT-READ](../security/CAP-OBJECT-READ-2026-10-18.md) bounds worker input and measures two ordinary PDF/OCR workers; remaining API/download reads and worst-case decoding remain open. Preserve command identity and supported partial outcomes. The pre-parser controls and native disk-spool evidence are [recorded separately](../security/CAP-INGRESS-BOUNDARIES-2026-10-18.md).
- **CAP-STORAGE:** Inventory orphan candidates without deleting anything, document reference ownership and restore expectations, and exercise supported local/S3 failure modes with synthetic bytes. Propose any lifecycle cleanup separately under approved retention policy; verify source hashes and cross-capture shared references.
- **PILOT-ACCEPT:** Extend the bounded pilot to an approved realistic corpus and deployment/key-provider configuration. Reconcile legal documents, bank application and accountant files after restore; measure resource ceilings and recovery. Distinguish synthetic evidence from accountant/provider/production acceptance.
- **V07:** Use the private masked inventory and approved verifier/denylist process; record per-value evidence and an exact backed-up cleanup proposal. Do not publish identifiers, self-approve synthetic status or rewrite shared history without scope approval.
- **CONTROL-ACCEPT:** Reconcile required source checks with actual GitHub/VPS/environment configuration read-only, record owner actions and verify external disclosure/backup/health/rollback outcomes. Preserve all security gates and obtain human/lead control review.
- **HISTORY-RECON:** Inventory affected historical mail and credit events privately, correlate independent relay/document evidence, and report unresolved outcomes. Propose an append-only correction/resend workflow for authorization; never rewrite audit chains or silently replay uncertain mail.
- **PRODUCT-BOUNDARIES:** Reconcile the specific decision below against current source before implementation. Obtain the stated policy/provider input where necessary, then define complete success, refusal and recovery behavior for each affected action, with tenant, currency and audit acceptance evidence.

## Image experiments and complete advisory snapshots

[Machine-readable comparison](../security/evidence/V05-CANDIDATE-COMPARISON-2026-10-17.json)
records immutable image/base IDs, all package versions/statuses, references and audit
hashes. Recorded CI artifact: `12105995790` (`final-image-security`). Scanner:
Trivy 0.76.0 pinned at `sha256:2416d1f74045b57274df1a340eee6b510cacd73c66a6ccb8da092b117eb7d222`,
linux/amd64. Occurrences count packages, not independent exploits. A listed finding
is not proof of application exploitability; no listed finding has been waived.

| Image / experiment | Observed result | Disposition |
|---|---|---|
| Latest SYS-04 final API/worker and frontend | 0 HIGH/CRITICAL in each final image | Runtime scopes passed; native qualification recorded |
| Latest SYS-04 build/service inventory | Backend builder 4 HIGH; frontend builder 18 HIGH; PG 24 HIGH + 1 CRITICAL; three unavailable refs | Full gate fails; [complete current inventory](../security/evidence/SYS-04-IMAGE-COVERAGE-2026-10-17.json) |
| Earlier SYS-03 rebuilt backend | 70 HIGH + 1 CRITICAL; 28 IDs; no listed fixed versions | Release blocked; [complete latest inventory](../security/evidence/SYS-03-STORAGE-VOLUME-2026-10-17.json) |
| Earlier CI final backend | 75 HIGH + 1 CRITICAL; 33 IDs; no listed fixed versions | Release blocked; complete earlier package inventory below |
| Recorded CI final frontend | 0 HIGH/CRITICAL | This scope passed; not a backend/build-stage clearance |
| Bookworm full application candidate | 100 HIGH + 4 CRITICAL; 50 IDs; no listed fixes | Rejected: worsens findings. Only both FROM lines changed in temporary Dockerfile; no production pin change |
| Alpine candidate | Python 3.14.8 starts; top-level `--no-deps` binary-wheel probe passed. Full build failed fetching APK indexes with DNS errors | Unqualified; no final-image scan or transitive/PDF/OCR compatibility claim. Package-resolution errors after failed indexes do not prove packages unavailable |
| Earlier build-stage assessment | 17 HIGH rows: 4 bundled npm, 13 native TypeScript compiler Go dependencies | Historical 16 October evidence; not rescanned here. Separate from final frontend and clean declared npm graph |

CI and local scans are separate executions; database freshness can differ. Counts
are observed results, not a controlled equal-database delta. Bookworm still fails
the unchanged gate and is not a remediation candidate.

**Subsequent SYS-03 rebuild:** the same base/requirements pins with refreshed OS
packages and scanner database report **70 HIGH + 1 CRITICAL / 28 IDs** on image
`aedaebcc6233`. [All latest package/version rows](../security/evidence/SYS-03-STORAGE-VOLUME-2026-10-17.json)
and five earlier IDs not observed in this snapshot are recorded. This is not an
applicability clearance or a controlled attribution to the volume repair. The
33-ID table below remains the complete earlier scan-time inventory, not the latest
candidate count. The subsequent SYS-04 final application images pass; the complete
eight-target inventory gate still fails on the residuals linked above.

Each advisory in the earlier backend snapshot remains **open / review required**. All fixed-version
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
| 24 | Historical identifier classification and empty denylist | V07 preparation complete, named private triage/cleanup still pending; the full-system scan reproduced the population after fetching all available branches |
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
That historical R021 baseline completed successfully for the application checks,
while its image gate failed. New documentation commits need their own CI status; previous
results identify the application baseline rather than claiming a future head passed.

## Boundaries and tracking rules

- The earlier approximately **93%** figure counted implemented rows in a small
  engineering table. Rows differ greatly in size; it is not percent effort, release
  readiness or the business/personal vision. Do not use it as a release estimate.
- R020/R021 refresh access at a documented point after I/O; they do not serialize
  every future revocation against commit/delivery or recall already received bytes.
  This is an explicit design boundary, not a newly reproduced bypass.
- Keep historical evidence dated: The initial image-comparison pass did not rerun V07/build scanning; the
  subsequent full-system scan refreshed both and records their exact scope. Rejected Bookworm and unqualified Alpine are experiments,
  not production repairs. No final-image finding is cleared by a clean pip/npm audit.
- A queue item needs status, evidence, next action and its acceptance checks. Log
  newly reproduced defects here before adding their implementation row to the
  backlog; retain original IDs and link fixes. Avoid duplicating owner decisions.
- No source, customer data, audit history, security exclusions, deployment or main
  merge changed in this documentation slice. Human/lead review remains required.
