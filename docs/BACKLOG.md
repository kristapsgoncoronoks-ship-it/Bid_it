# Engineering backlog

## Current findings and work order — 18 October 2026

The [known findings register](reviews/FINDINGS-REGISTER-2026-10-17.md) consolidates
confirmed findings, suspected defects, verification gaps, rejected image candidates,
owner decisions and implemented repairs. It preserves all 33 original scan-time
backend advisory IDs and links their exact package/version evidence. Work through its queue
one item at a time; blocked inputs stay visible while the next unblocked item proceeds.
The [SYS-04 runtime repair and expanded audit](security/SYS-04-IMAGE-COVERAGE-2026-10-17.md)
observes **zero HIGH/CRITICAL in both final application images**. The full gate
remains blocked: backend builder 4 HIGH, frontend builder 18 HIGH, PostgreSQL
24 HIGH + 1 CRITICAL and three unavailable service references. Earlier inventories
remain preserved; all 47 current advisory/package rows are in the new evidence.

**SYS-01 OIDC destination validation is implemented, pending CI/human review:**
HTTPS/connect-time public-address pinning, issuer binding, no proxy/redirect bypass
and sanitized failures; [repair evidence](security/SYS-01-OIDC-DESTINATIONS-2026-10-17.md).
**SYS-02 Compose readiness is implemented, pending CI/human review:** Python probes
preserve database readiness and timings; six real renders and native-image
failure/recovery/dependency checks passed. [Evidence](security/SYS-02-COMPOSE-READINESS-2026-10-17.md).
**SYS-03 fresh-volume permissions is implemented, pending CI/human review:** private
UID/GID 10001 mount target, real-image CI gate and native API/worker/refusal/restart
[evidence](security/SYS-03-STORAGE-VOLUME-2026-10-17.md). Existing nonempty ownership
is preserved; incompatible legacy volumes need an operator-reviewed migration.
**SYS-04 runtime repair/inventory engineering is implemented, pending CI/review:**
qualified Alpine Python/OCR runtime, patched origin libexpat, pinned existing PG16
digest and fail-closed build/final/manifest-service scanning. Vendor build findings
and MinIO/PgBouncer qualification remain [Decisions §29](DECISIONS-NEEDED.md#29-residual-nativebuild-image-findings-v05b-2026-10-16).
**CAP-RETRY is implemented, pending CI/human review:** durable capture generations,
keyed retry jobs, atomic result/evidence publication and fenced progress preserve
newer attempts and reviewed/saved data. [Repair and evidence](plan/CAP-RETRY-ATTEMPT-FENCING-2026-10-18.md).
**CAP-COMMAND is implemented, pending CI/human review:** durable per-file receipts,
payload-bound replay before quota, authorized status lookup and account/workspace
browser recovery after response loss or refresh. Purging a capture retains its
receipt. [Repair and evidence](plan/CAP-COMMAND-DURABLE-ADMISSION-2026-10-18.md).
**Next unblocked:** CAP-RESOURCE. The register has one ordered queue and per-item prompts. Eleven
other unbounded upload reads, Kubernetes candidates and PG schema-drift tooling
are also logged by the scan. New follow-ups CAP-REVIEW-VERSION (stale completed
drafts), SYS-08 (historical PostgreSQL downgrade portability) and SYS-09 (browser
repeatability) are in the register. SYS-01–SYS-04 are subsequent repairs; nothing is release-cleared.
Older row labels below retain implementation-time wording; use the register for
current status. The earlier ~93% row-count estimate is not release readiness or
product-vision progress.

The single, controlled list of **build work and tech-debt that is unblocked** — i.e.
things an engineer can pick up now without waiting on anyone. It is deliberately
scoped to complement the two registers that already exist, not duplicate them:

| Register | What lives there |
|---|---|
| **This file** | Unblocked engineering work + tech-debt, prioritised Now / Next / Later. |
| [`DECISIONS-NEEDED.md`](DECISIONS-NEEDED.md) | Work **built to a boundary** that can't finish without an external **decision, credential, or infra** (SSO/SCIM/SAML live, billing go-live, ERP exporters, residency, prod KEK, public API GA, SOC 2 / ISO). |
| [`product/product-requirements.md` §10](product/product-requirements.md) | **Product** priorities (Must / Should / Could / Later) — the *what to sell*, not the *what to build next*. |

An item leaves this file when it ships (move it to **Recently shipped**) or when it
turns out to need a decision (move it to `DECISIONS-NEEDED.md`). Keep each item to
one line of *what* + one of *why* + a size and a source, so the list stays scannable.

Size: **S** ≈ <½ day · **M** ≈ 1–3 days · **L** ≈ >3 days. Every item must land with
tests and keep CI green.

---

## Security remediation — Now, merge blockers

Queued from the [2026-10-16 vulnerability analysis and fix plan](security/VULNERABILITY-ANALYSIS-AND-FIX-PLAN-2026-10-16.md).
**V01a and V01b are implemented locally, pending CI/review**; V02/V03, V04, V05a, V06a and V07a are implemented locally. V05b has residual findings requiring review; nothing is release-cleared. [V01a evidence](security/V01A-PYPDF-UPGRADE-2026-10-16.md). Clean isolated
candidate audits alone are not implemented fixes. This sequence takes priority over routine feature expansion.
Engineering prompts, advisory evidence and full acceptance checks remain in the
linked plan; these rows track the work without duplicating the findings register.

| # | Item / completion criterion | Why / dependencies | Size | Source |
|---|---|---|---|---|
| V01a · P0 | **Upgrade pypdf narrowly to audited 6.21.0 — implemented locally.** 203 PDF/intake/output regressions and Python 3.11/3.14 library smoke checks passed; fresh audit has no pypdf findings. [Evidence](security/V01A-PYPDF-UPGRADE-2026-10-16.md); JWT repair is recorded below; the residual image gate still blocks release. | First repair: eight observed advisories; untrusted uploads reach affected parsing. Keep unrelated backend upgrades out. | S | Plan §1 / V01 |
| V01b · P0 | **Bound untrusted PDF parsing in a killable process — implemented locally.** Input/decoded-output, attachment-count, CPU/memory/wall limits, progress IPC and explicit persisted failure/recovery; [evidence](security/V01B-PDF-PROCESS-2026-10-16.md). | Continue V01a. Existing async/thread deadlines and the separate preview renderer do not bound attachment extraction. | M | Plan §1 / V01 |
| V02/V03 · P1 | **Replace python-jose with PyJWT[crypto] 2.15.1 and require OIDC claims — implemented locally.** Missing-claim regressions reproduced first; legacy HS256/rotation, RSA-only OIDC, strict claims/JWKS and production key policy verified. Fresh runtime/dev audit has zero findings; obsolete ECDSA ignore removed. [Evidence](security/V02-V03-JWT-REPAIR-2026-10-16.md). | Next auth repair; no patched jose release listed, and missing-claim acceptance was reproduced. Include production key/algorithm policy; no new audit exceptions. | M | Plan §2 / V02–V03 |
| V05a · P0/P1 | **Replace the frontend NGINX base — implemented locally, pending CI/review.** Maintained digest, non-root origin, TLS/proxy/static/document/header/upload-limit smoke passed; final image has zero high/critical findings. [Evidence](security/V05-IMAGES-2026-10-16.md). | Can run alongside auth work. Base-image findings require deployment/architecture assessment; candidate base scan alone is not runtime sign-off. | S | Plan §3 / V05 |
| V04 · P1 | **Update only the source-map-js lock resolution to 1.2.2 — implemented locally.** Full npm audit clean; install/lint/build/bundle passed. Current application-head CI: 581 browser and 13 visual checks passed; final combined gate/review remains required. [Evidence](security/V04-FRONTEND-LOCK-2026-10-16.md). | Independent small repair; six high package entries share one build-tool advisory. Do not take the whole frontend upgrade group or use audit-fix force. | S | Plan §4 / V04 |
| V05b · P1 | **Runtime images repaired; complete inventory gate remains blocked.** Alpine API/OCR and patched NGINX libexpat scan zero HIGH/CRITICAL; native compatibility passed. Backend builder 4 HIGH, frontend builder 18 HIGH, PG gosu 24 HIGH + 1 CRITICAL and three unavailable service refs remain. [Current complete evidence](security/SYS-04-IMAGE-COVERAGE-2026-10-17.md); previous snapshots preserved, no waiver. | Supported patched vendor builds or qualified service selection/lead review under Decisions §29; runtime-only clean scans do not clear release. | M | Plan §3 / V05 |
| V06a · P1 | **Restore functioning dependency coverage in CI — implemented locally, control review pending; current application-head CI passed.** Full Python/npm graphs, resolved/locked base-head deltas and six vulnerable/fixed/error fixtures. [Coverage and evidence](security/V06-SUPPORTED-DEPENDENCY-REVIEW-2026-10-16.md); no hosted entitlement required. | Engineering is unblocked. Hosted feature availability/settings are tracked separately in [Decisions §28](DECISIONS-NEEDED.md); retain severity, transitive/dev scope and workflow/pin controls. | M | Plan §5 / V06 |
| V07a · P1 | **Prepare private history-PII triage — implemented locally.** 872 masked rows / 196 unresolved blobs across 19 fetched/local refs; private inventory and specific cleanup proposal prepared. [Evidence](security/V07-PRIVATE-TRIAGE-PREPARATION-2026-10-16.md). Named verification/denylist/history action remain Decisions §24; no automatic synthetic approval. | Engineering preparation is unblocked. Named verification, the owner-held denylist and any shared-history rewrite remain in [Decisions §24](DECISIONS-NEEDED.md). A clean current tree does not resolve history. | M | Plan §6 / V07 |

**Next:** current-head verification and review; V05b residual findings remain
blocked under Decisions §29, V06a control review under §28, and V07 cleanup under
§24. The report-driven E16/Function Studio/R014–R016 repairs and bounded pilot are
implemented below; complete current-source verification/review while preserving
these release blockers. Re-scan candidate versions before implementation; recorded clean scans
are dated evidence, not a guarantee about future releases or deployed images.

**Exit / merge gate:** the exact reviewed head must pass fresh dependency,
workflow, backend, PostgreSQL, browser, bundle and final-image checks. Test the
CI/local/Docker runtime matrix specified by the plan. Record explicit PII-history
triage and any evidence-based residual image findings. Keep main merge blocked
until required controls pass; no audit skips, blanket exclusions or budget rises.
External dependencies remain visible in the decision register, not marked done.

## Review repairs — after security verification

Source through R019 is merged via [PR #62](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/pull/62)
into main `7608c1e`. R020/R021 remain the follow-up draft candidate. Merged source
does not mean release clearance: the evidence/review labels below retain their
security and external-acceptance limits.

Reconciled the two newly uploaded ECC/Function Studio reports against current source;
[evidence, source hashes and per-item prompts](reviews/RECONCILIATION-2026-10-16.md).
These rows track implemented repairs and pending verification/review, not completed release claims.
The reports’ E16/V labels do not replace the security V01–V07 IDs above.
[Combined application verification and remaining gates](reviews/FINAL-VERIFICATION-2026-10-16.md).

| Existing ID | Work / completion criterion | Priority / size |
|---|---|---|
| E16-03 | **Implemented locally, pending CI/review:** shared EUR ledger projection, provenance/incomplete states, guarded plan/final billing and preserved frozen/archive history. EUR190 and source download verified through real browser/API/PG; [evidence](plan/E16-PROJECT-CURRENCY-2026-10-16.md). | P1 / M |
| R014 / INV18 / E16-02 | **Implemented locally, pending CI/review:** complete-or-refused ZIP, filters before 500/501 bound, manifest/hashes, byte caps and refreshed authorization. Real browser/PDF/PG evidence: [repair record](plan/R014-COMPLETE-INVOICE-ZIP-2026-10-16.md). | P1 / M |
| FP-P01 / FS16-01/02 | **Implemented locally, pending CI/review:** immutable version-pinned execution, typed/bounded validation, revision conflicts and explicit legacy mismatch preflight. [Repair evidence](plan/FP-P01-PUBLISHED-RULES-2026-10-16.md). | P1 / M |
| R008-A-AU04 / FS16-03 | **Implemented locally, pending CI/review:** lossless raw fallback for string IDs/comma-list members/nested conditions; pending/readback and stale-edit preservation. [Evidence](plan/FP-P01-PUBLISHED-RULES-2026-10-16.md). | P1 / M |
| R008-A-AU06 / FS16-04 | **Implemented locally, pending CI/review:** named draft/published preview basis, shared suppression/cooldown/cap decisions, bounded proposals and zero action writes. [Evidence](plan/FS16-04-TRUTHFUL-PREVIEW-2026-10-16.md). | P1 / M |
| FP-P02 / FS16-05 | **Engineering implemented locally, pending CI/review:** durable version-pinned children/native mail jobs, truthful states and partial recovery. [Evidence/limits](plan/FP-P02-DURABLE-AUTOMATION-2026-10-16.md); historical/uncertain resend policy remains [decision 30](DECISIONS-NEEDED.md#30-historical-automation-and-uncertain-smtp-evidence-fs16-05). | P1 / L |
| R015 / INV20–21 / E16-04 | **Implemented locally, pending CI/review:** bounded multipart/file reads, offloaded fail-closed scan, durable upload/status lookup and per-file download recovery. PG race and real browser byte/hash [evidence](plan/R015-ATTACHMENT-COMMANDS-2026-10-16.md). | P2 / M |
| R016 / pilot finding | **Implemented locally, pending CI/review:** keyed legal credit issuance/status lookup and reviewed-balance conflicts; partial/full retries converge without duplicate notes; historical audit links require [decision 32](DECISIONS-NEEDED.md#32-historical-credit-audit-document-links-r016). [Evidence and prompt](plan/R016-CREDIT-COMMANDS-2026-10-16.md). | P1 legal-document gate / M |
| E16-05 | **Bounded engineering pilot passed, pending review/external acceptance:** real capture/approval/bank-match/CSV journey, isolated data restore plus new upload/worker check, enforced 2-CPU/1,920-MiB server ceilings. [Measured evidence and limits](plan/E16-05-CONTROLLED-PILOT-2026-10-16.md); no production sizing or accountant-acceptance claim. | P1 pilot gate / M |
| R017 / intake follow-up | **Implemented locally, pending CI/review:** cap+1 capture reads, explicit storage refusal/per-file retry, ordered same-name source recovery and isolated local staging files. [Evidence and prompt](plan/R017-CAPTURE-ADMISSION-2026-10-17.md). | P1 resource/recovery / M |
| R018 / intake transaction | **Implemented locally, pending CI/review:** per-file registry/capture/job/usage commit together; failure rolls back the current file and preserves earlier accepted batch files. Separate-connection PostgreSQL worker visibility and retry [evidence/prompt](plan/R018-ATOMIC-CAPTURE-ADMISSION-2026-10-17.md). | P1 consistency/recovery / M |
| R019 / intake quota | **Implemented locally, pending CI/review:** final per-workspace PostgreSQL admission lock and fresh plan/policy/usage check prevent concurrent last-slot oversubscription; rollback releases capacity and other workspaces progress. Single/batch refusal and native-worker [evidence/prompt](plan/R019-CAPTURE-QUOTA-CONCURRENCY-2026-10-17.md). | P1 concurrency / M |
| R020 / intake access | **Implemented locally, pending CI/review:** pinned per-file identity refresh after scan/lock waits and storage; committed revocations/switches refuse new capture/job/usage without releasing the quota lock or exposing duplicate details. Earlier accepted batch work and registry metadata survive; [evidence/prompt](plan/R020-CAPTURE-ACCESS-REFRESH-2026-10-17.md). | P1 authorization / M |
| R021 / capture downloads | **Implemented locally, pending CI/review:** original/page reads refresh access and recheck visibility after storage; source SHA-256/registry size and current page identity must verify before response. Private/no-store originals and explicit corruption refusal; [evidence/prompt](plan/R021-CAPTURE-DOWNLOAD-TRUTH-2026-10-17.md). | P1 privacy/integrity / M |

## Review implementation sequence

The user-supplied development review prioritized these repairs after the N1
supplier-identifier slice. They are implemented and tested locally; this is not
a shipped-release status.

- [R011: invoice commands and delivery truth](plan/R011-INVOICE-COMMANDS-2026-10-13.md).
- [R002: scoped generic accountant preview and download](plan/R002-ACCOUNTING-PREVIEW-2026-10-13.md); provider adapters remain blocked on reviewed mappings/templates.
- [R012: replay-safe direct cash and explained correction](plan/R012-CASH-COMMANDS-2026-10-13.md).
- [N4: bounded capture page previews](plan/N4-CAPTURE-PAGES-2026-10-14.md).
- [Review repair: reconciliation source-currency display and CSV retention](plan/RECON-CURRENCY-2026-10-14.md).
- [Remaining ECC security repairs](plan/ECC-SECURITY-2026-10-15.md): session tenancy, stale-tab refusal, calendar offboarding and browser-bound one-use OIDC; implemented locally, pending CI/review.
- [ECC account-cache isolation](plan/ECC-IDENTITY-CACHE-2026-10-15.md): fresh client ownership per identity transition and quarantine of late responses; follow-on backend repairs are linked above.
- [N1: captured versus calculated line tax/gross](plan/N1-LINE-AMOUNTS-2026-10-13.md), continuing the intake task; bank-account controls remain separate.

## Now — pick these up next (small, high-value, unblocked)

| # | Item | Why | Size | Source |
|---|------|-----|------|--------|
| N1 | **Capture the remaining invoice fields**: ~~supplier registration no. + VAT~~ implemented locally (2026-10-13; BT-30/BT-31 document snapshots, structured/export capture, provenance, guarded review; [evidence](DEVELOPMENT-REVIEW-2026-10-13.md), pending CI/review); **supplier IBAN document snapshot** implemented locally (2026-10-16; qualified CSV/JSON and UBL/CII payee capture, guarded review, masked detail/audits, no learned bank suggestions or vendor/payment updates; [evidence](plan/N1-SUPPLIER-IBAN-2026-10-16.md), pending CI/review); ~~per-line **tax amount** + **line gross**~~ implemented locally (captured/calculated snapshots, guarded review and provenance; [evidence](plan/N1-LINE-AMOUNTS-2026-10-13.md), pending CI/review). <br>~~PO number~~ ✅ **SHIPPED** (2026-09-13) — `invoices.po_reference`, EN-16931 **BT-13**, read from UBL `cac:OrderReference/cbc:ID` and CII `BuyerOrderReferencedDocument/IssuerAssignedID`, plus the CSV/JSON spellings in `parser.PO_REFERENCE_KEYS`; provenance row, list + detail shapes, editable and clearable in review (`tests/test_n1_po_reference.py`). <br>⚠ **IBAN sub-item carries a constraint**: PRD forbids logging secrets/IBANs and requires bank data masked in analytics — that field is not a plain additive column. | The intake slice captures only a subset; PRD §5A lists these. Additive model + parser + schema + provenance. | M | Intake slice (commit `ab52df4`) deferral |
| N4 | **Page thumbnails for captures.** Implemented locally (2026-10-14; bounded isolated rendering, tenant-scoped JPEG storage/serving, page navigation and original fallback; [evidence](plan/N4-CAPTURE-PAGES-2026-10-14.md), pending CI/review). | Reviewers need to see the source page next to the extracted draft; today there's nothing to show. | M | Intake slice deferral |

## Next — valuable, a bit larger

| # | Item | Why | Size | Source |
|---|------|-----|------|--------|
| X1 | ~~**Multi-file / batch upload.**~~ ✅ **SHIPPED** (WO-X) — `POST /invoices/upload/batch` with a visible per-request file cap and per-file reasons; FE multi-select in `e2e/upload-batch.spec.ts`. | — | — | verified 2026-09-13 |
| X2 | ~~**Upload progress.**~~ ✅ **SHIPPED** (WO-X) — `services/capture_progress.py` stages + percent on the capture run, reset per attempt so a re-parse reports phases again; `e2e/capture-progress.spec.ts`. | — | — | verified 2026-09-13 |
| X3 | ~~**SSO client secret → keyvault.**~~ ✅ **SHIPPED** — `core/keyvault.py` (AES-256-GCM, AAD-bound, `kv1.` format, ADR-0016); sealed on write in `sso_config.py`, unsealed at use in `oidc.py`; `models/sso.py:17` now documents sealed-at-rest and the TODO marker is gone. | — | — | verified 2026-08-27 |
| X4 | ~~**Visual-regression in CI.**~~ ✅ **SHIPPED** (WO-Y) — VR runs as a real CI gate in the containerised browser, with a `vr-baselines` workflow to regenerate baselines inside the same container. | — | — | verified 2026-09-13 |

## Later — larger or lower-priority (unblocked)

| # | Item | Why | Size | Source |
|---|------|-----|------|--------|
| L1 | **External extraction provider — moved to [Decisions §31](DECISIONS-NEEDED.md#31-external-extraction-provider-l1).** Existing deterministic/OCR provider and confidence plumbing remain available. | Selecting a paid processor, data handling terms, region and credentials requires an owner decision before implementation; this is not unblocked engineering. | L / blocked | Provider interface (commit `ab52df4`) |
| L2 | **Header multi-rate VAT breakdown on received invoices.** Implemented locally (2026-10-15): saved-line rate groups, exact line-rounding sums, missing-history and header-mismatch guidance in detail/review; [evidence](plan/L2-RECEIVED-VAT-2026-10-15.md), pending CI/review. | Multi-rate suppliers; PRD §5D. | M | Intake gap analysis |

> **Blocked on you** (not in this file): enterprise SSO/SCIM/SAML live-testing, billing
> go-live, DATEV/SAF-T exporters, data residency, production KEK, public API GA, and
> SOC 2 / ISO — all tracked with "what I need from you" in
> [`DECISIONS-NEEDED.md`](DECISIONS-NEEDED.md).

---

## Recently shipped (context — not backlog)

- **The supplier's purchase-order reference survives arrival** (N1, part): `IssuedInvoice`
  had carried EN-16931 **BT-13** since the issuing module shipped, but the received
  `Invoice` had no order reference at all — `einvoice.py` genuinely parses inbound UBL
  and CII and read straight past the element, so a supplier could send the field, in the
  standard, in the place the standard puts it, and the number was discarded. Nothing
  downstream could match a supplier invoice to a purchase order. Now captured from UBL
  `cac:OrderReference/cbc:ID` (read from that element ONLY — `cbc:ID` is everywhere in a
  UBL document and a looser search files the invoice number as its own PO), from CII
  `BuyerOrderReferencedDocument/IssuerAssignedID`, and from the CSV/JSON spellings in
  `parser.PO_REFERENCE_KEYS`; absent stays absent (`missing` provenance, never
  `defaulted` — there is no sensible default for someone else's order number). Same
  column width as the issued side (60) so a reference round-trips through either half of
  the product, trimmed at capture rather than failing the intake. On the list shape as
  well as the detail, because an approver matches while scanning the queue, and
  clearable in PATCH via `model_fields_set` so a mis-captured number is not permanent.
  Migration `d3e4f5a6b7c8` (additive, nullable, symmetric downgrade).
  18 tests (`tests/test_n1_po_reference.py`) + 2 e2e (`e2e/invoice-po-reference.spec.ts`).
- **Supplier-invoice intake slice** (`ab52df4`): pluggable extraction providers, honest
  per-field provenance (confidence / original / normalized / reviewed / low-confidence),
  JPEG/PNG OCR intake, same- vs cross-supplier duplicate detection, human-review queue +
  manual re-extract, corrupt-file hardening. 12 scenario tests.
- **One upload size cap** (N3, WO-94): `filesec.max_bytes(purpose=None)` is the single
  definition of the limit and `too_large_message()` renders the sentence from the same
  number, so no caller can quote a figure it does not enforce. Seven hard-coded caps in
  six route modules are gone — three duplicated `settings.max_upload_mb` (so raising it
  did nothing on the primary capture endpoint), two are now clamped purpose policy
  (`PURPOSE_MB`: receipt 5 MB, logo 2 MB), and two 25 MB `_ATTACH_MAX` constants were
  DEAD, since `reject_active_content` already capped those paths at 15 MB. An AST scan
  over the whole `app/` package refuses a second cap, with seeded-violation self-tests
  (`tests/test_wo94_upload_cap.py`).
- **Integrity-cover the original uploads** (N2): `verify_documents` now sweeps the
  `uploads` prefix over `extraction_runs.source_sha256`, so the stored original
  supplier-invoice bytes (the legal record) are re-hashed alongside receipts / logos /
  email-attachments — silent loss or corruption of an original is now a finding.
- **Frontend design system + shell + gallery** (`256da1a`), with Playwright e2e smoke
  gated in CI (`419edfb`).
- **Identity / authz**: 8-role matrix + guard migration (`3f09d5f`), multi-org membership,
  sessions, cross-tenant isolation proof.
- **CI health**: Postgres boolean-default migration fix (`0708c16`), Node-24 actions bump.
