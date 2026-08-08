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

## Now — pick these up next (small, high-value, unblocked)

| # | Item | Why | Size | Source |
|---|------|-----|------|--------|
| N1 | **Capture the remaining invoice fields**: supplier registration no. + VAT, PO number, bank account / IBAN as first-class captured fields; per-line **tax amount** + **line gross**. | The intake slice captures only a subset; PRD §5A lists these. Additive model + parser + schema + provenance. | M | Intake slice (commit `ab52df4`) deferral |
| N4 | **Page thumbnails for captures.** Render page images at capture time (the OCR path already rasterises via `pypdfium2` but discards them) and persist to object storage; serve via `/doc`. | Reviewers need to see the source page next to the extracted draft; today there's nothing to show. | M | Intake slice deferral |

## Next — valuable, a bit larger

| # | Item | Why | Size | Source |
|---|------|-----|------|--------|
| X1 | **Multi-file / batch upload.** One endpoint accepting N files → N capture runs (or document the client-loop as the contract), plus FE drag-drop multi-select plumbing. | Bulk intake is a stated capability; today it's one request per file. | M | Intake request |
| X2 | **Upload progress.** Server signal for long OCR jobs — SSE or a lightweight poll contract the FE can show a real progress bar against (the async 202 + poll model already supports it). | UX for large scanned PDFs. | M | Intake request |
| X3 | **SSO client secret → keyvault.** Move the SSO secret out of its plain column into the envelope-encrypted `keyvault` (ADR-0016). | Security tech-debt — a stored OAuth secret shouldn't sit in cleartext. | M | `models/sso.py:37` (`# TODO: secret store`) |
| X4 | **Visual-regression in CI.** Containerise the Playwright VR baselines so `npm run test:vr` gates in CI (today CI runs smoke only; VR is a local gate because pixel baselines are browser-build specific). | Catch unintended UI drift automatically. | M | `docs/DESIGN_SYSTEM.md` §6 |

## Later — larger or lower-priority (unblocked)

| # | Item | Why | Size | Source |
|---|------|-----|------|--------|
| L1 | **A real extraction provider.** Plug an OCR / document-AI vendor behind the shipped `ExtractionProvider` interface (`services/extraction_provider.py`) with confidence surfaced per field. | The interface + honest-confidence plumbing exist; there's no non-deterministic backend yet. Vendor/cost choice makes this partly a decision — promote to `DECISIONS-NEEDED.md` when a candidate is picked. | L | Provider interface (commit `ab52df4`) |
| L2 | **Header multi-rate VAT breakdown on received invoices.** `vat.compute` groups rates on the *outbound* side only; mirror it for received invoices (multiple tax rates per invoice). | Multi-rate suppliers; PRD §5D. | M | Intake gap analysis |

> **Blocked on you** (not in this file): enterprise SSO/SCIM/SAML live-testing, billing
> go-live, DATEV/SAF-T exporters, data residency, production KEK, public API GA, and
> SOC 2 / ISO — all tracked with "what I need from you" in
> [`DECISIONS-NEEDED.md`](DECISIONS-NEEDED.md).

---

## Recently shipped (context — not backlog)

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
