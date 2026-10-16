# Engineering backlog

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
**V01a is implemented locally, pending CI/review**; the remaining items are not
started. [V01a evidence](security/V01A-PYPDF-UPGRADE-2026-10-16.md). Clean isolated
candidate audits alone are not implemented fixes. This sequence takes priority over routine feature expansion.
Engineering prompts, advisory evidence and full acceptance checks remain in the
linked plan; these rows track the work without duplicating the findings register.

| # | Item / completion criterion | Why / dependencies | Size | Source |
|---|---|---|---|---|
| V01a · P0 | **Upgrade pypdf narrowly to audited 6.21.0 — implemented locally.** 203 PDF/intake/output regressions and Python 3.11/3.14 library smoke checks passed; fresh audit has no pypdf findings. [Evidence](security/V01A-PYPDF-UPGRADE-2026-10-16.md); remaining auth findings still block merge. | First repair: eight observed advisories; untrusted uploads reach affected parsing. Keep unrelated backend upgrades out. | S | Plan §1 / V01 |
| V01b · P0 | **Bound untrusted PDF parsing in a killable process.** Enforce input/decoded-output, attachment-count, CPU/memory/wall limits; persist explicit failure and prove another job can proceed. | Continue V01a. Existing async/thread deadlines and the separate preview renderer do not bound attachment extraction. | M | Plan §1 / V01 |
| V02/V03 · P1 | **Replace python-jose with PyJWT[crypto] 2.15.1 and require OIDC claims.** Add missing-exp/aud/sub/iat rejection regressions first; preserve legacy HS256 sessions, key rotation, RSA-only OIDC, nonce/browser binding and tenant guards. Remove the old ECDSA ignore only after the dependency disappears and audit passes. | Next auth repair; no patched jose release listed, and missing-claim acceptance was reproduced. Include production key/algorithm policy; no new audit exceptions. | M | Plan §2 / V02–V03 |
| V05a · P0/P1 | **Replace the frontend NGINX base with the audited maintained candidate, pinned by digest.** Validate TLS/proxy/static/document headers, upload limits, health and rollback; scan the final image. | Can run alongside auth work. Base-image findings require deployment/architecture assessment; candidate base scan alone is not runtime sign-off. | S | Plan §3 / V05 |
| V04 · P1 | **Update only the source-map-js lock resolution to 1.2.2.** Review optional-binding metadata; pass full npm audit, install, lint/build/browser and unchanged bundle budgets. | Independent small repair; six high package entries share one build-tool advisory. Do not take the whole frontend upgrade group or use audit-fix force. | S | Plan §4 / V04 |
| V05b · P1 | **Audit and repair final Python/Node images and runtime controls.** Resolve fixable packages, assess unfixed cases individually, verify non-root/service capabilities and record SBOMs/digests with health/OCR/startup evidence. | Coordinate with V01a, V02/V03 and V05a to avoid repeated rebuilds; base-only scans omit installed app/OCR packages. | M | Plan §3 / V05 |
| V06a · P1 | **Restore functioning dependency coverage in CI.** Diagnose hosted-service support; implement a supported equivalent if that route is selected. Prove vulnerable fixtures fail, corrected graphs pass and service/configuration errors fail closed. | Engineering is unblocked. Hosted feature availability/settings are tracked separately in [Decisions §28](DECISIONS-NEEDED.md); retain severity, transitive/dev scope and workflow/pin controls. | M | Plan §5 / V06 |
| V07a · P1 | **Prepare private history-PII triage.** Inventory affected blobs/refs, distinguish provable synthetic fixtures from unresolved matches, and produce a specific cleanup proposal without exposing raw identifiers. | Engineering preparation is unblocked. Named verification, the owner-held denylist and any shared-history rewrite remain in [Decisions §24](DECISIONS-NEEDED.md). A clean current tree does not resolve history. | M | Plan §6 / V07 |

**Next:** V01b, then V02/V03; V01a is implemented locally. V05a and V04 remain
independent where practical. Finish V05b after the relevant package/base choices.
V06a and V07a preparation can proceed in parallel without waiting on external
inputs. Re-scan candidate versions before implementation; recorded clean scans
are dated evidence, not a guarantee about future releases or deployed images.

**Exit / merge gate:** the exact reviewed head must pass fresh dependency,
workflow, backend, PostgreSQL, browser, bundle and final-image checks. Test the
CI/local/Docker runtime matrix specified by the plan. Record explicit PII-history
triage and any evidence-based residual image findings. Keep main merge blocked
until required controls pass; no audit skips, blanket exclusions or budget rises.
External dependencies remain visible in the decision register, not marked done.

## Local review sequence — pending CI and merge

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
| L1 | **A real extraction provider.** Plug an OCR / document-AI vendor behind the shipped `ExtractionProvider` interface (`services/extraction_provider.py`) with confidence surfaced per field. | The interface + honest-confidence plumbing exist; there's no non-deterministic backend yet. Vendor/cost choice makes this partly a decision — promote to `DECISIONS-NEEDED.md` when a candidate is picked. | L | Provider interface (commit `ab52df4`) |
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
