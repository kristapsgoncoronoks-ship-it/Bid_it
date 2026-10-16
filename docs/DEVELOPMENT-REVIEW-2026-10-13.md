# Documentation review and first development slice — 2026-10-13

## Scope and interpretation

The initial inventory covers **310 repository Markdown files / 90,949 lines**,
including `.ai-review/` and both PR templates, excluding dependencies, Git
metadata, and generated build directories. Every file received a structural
scan of headings and relative Markdown links; the complete per-file results are
in [MARKDOWN-INVENTORY-2026-10-13.md](MARKDOWN-INVENTORY-2026-10-13.md).
The governing engineering rules, current backlog, product requirements,
architecture decisions, development queues, and relevant implementation records
received detailed review against the source. This is not a claim that every
historical paragraph or historical test result was independently revalidated.

| Documentation group | Files | Development implication |
|---|---:|---|
| Architecture and decisions | 43 | Keep the modular monolith, async services, tenant boundaries, structural permissions, Decimal money, durable jobs, and audit conventions. |
| Product and design | 19 | The sellable workflow is capture → review → classify → export; remaining intake gaps directly support it. |
| Audit and security | 29 | Findings are evidence at their recorded commit; check current code before treating a historical finding as an open defect. |
| Historical work orders | 119 | Preserve acceptance criteria and rationale; do not restart already shipped work or the shelved greenfield plan. |
| Reference investigations | 54 | Learn native design principles; archive patches target older revisions and are not installation instructions. |
| Plans and operations | 46 | Distinguish unblocked engineering tasks from owner decisions, production operations, and historical release gates. |

The scan found **no missing relative inline-link destinations**. It does not
validate external URLs, heading anchors, prose paths, or reference-style links.

## Which documents govern the next task

1. [Engineering backlog](BACKLOG.md): the compact, explicitly unblocked Now / Next / Later queue.
2. [Engineering rules](architecture/engineering-rules.md) and [AI engineering policy](architecture/AI-ENGINEERING-POLICY.md): implementation, testing, security, migration, and evidence requirements.
3. [Product requirements](product/product-requirements.md), accepted ADRs, and designs: behavior and constraints.
4. [Decisions needed](DECISIONS-NEEDED.md): prerequisites engineering must not invent.
5. [TODO](../TODO.md), work orders, audits, and reference bundles: historical context whose status must be reconciled with the current tree.

## Findings from reconciling the plans

- **Plan A is decided.** [The decision sheet](plan/PLAN_A_vs_PLAN_B.md) says Plan A was selected on 2026-07-25. The execution hub still labels that choice OPEN in its tree listing. Plan B is an alternative retained for history.
- **Several release-gate statements are superseded inside their own documents.** [Release readiness](RELEASE-READINESS.md) records runner restoration, merging, and completed tooling while later historical checklists still show some of those actions unchecked. These are not instructions to repeat repository or production operations.
- **Archive status is not present readiness.** Reference bundles describe patch construction and checks against older commits. The current [reference integration register](plan/REFERENCE-INTEGRATION-PLAN.md) records native application and later evidence.
- **The active build queue is small.** BACKLOG Now contains N1 (remaining supplier/line capture fields) and N4 (page thumbnails). PO references, batch uploads, progress, keyvault storage, and visual regression are already shipped. Source inspection confirms the existing PO capture and missing supplier-identifier snapshots.
- **Commercial and enterprise blockers remain separate.** Live billing, real IdP validation, production key custody, residency infrastructure, and filing/shadow-run evidence need their documented external prerequisites. This task supplies none of those decisions or credentials.

## Selected development task: N1 supplier identifiers

The user asked to choose from repository plans. The first bounded N1 slice adds
supplier registration number (**BT-30**) and supplier VAT identifier (**BT-31**)
to received invoices, independent of the mutable vendor master.

Behavior:

- UBL capture reads seller `PartyLegalEntity/CompanyID`; VAT capture requires a seller `PartyTaxScheme` whose `TaxScheme/ID` is `VAT`.
- CII capture reads the header agreement's seller `SpecifiedLegalOrganization/ID`; VAT capture requires seller `SpecifiedTaxRegistration/ID` with `schemeID="VA"`. `FC` fiscal identifiers and buyer identifiers are not used as VAT identifiers.
- CSV/JSON accept supplier-qualified aliases. Leading zeroes survive; absent or blank identifiers stay null. Capture trims oversized text to the 80-character storage limit.
- Header provenance records extracted or missing values. Existing async capture, human corrections, and invoice confirmation retain the values.
- Nullable invoice columns expose the snapshots on list/detail and approval-review responses. Existing rows remain null; nothing is guessed from vendor records.
- Capture review and invoice review show editable fields; invoice detail and locked review show values when present. Existing version and workflow guards protect saved-invoice corrections, including explicit null clearing.
- No vendor-master update, payment change, new route, dependency, or external network call is introduced.

Migration `e4f5a6b7c8d9` adds the two nullable columns after `d3e4f5a6b7c8`.
Application rollback can leave them in place; database downgrade removes their
captured values, so export those values before downgrading if retention matters.
The generated OpenAPI change is additive and retained for review.

## Validation evidence

- Initial relevant backend regression: **82 passed**, covering N1/PO capture, extraction, field provenance, capture-review API, invoice-review workflow, and migrations including upgrade/downgrade/re-upgrade and model parity.
- Final new supplier-identifier suite: **23 passed**, including seller/buyer and VAT/fiscal distinctions, aliases, blanks, truncation, persistence, list/review shapes, correction, null clearing, stale-version rejection, approved-state rejection, vendor-master preservation, and async capture provenance.
- Browser detail checks: **4 passed** using the installed Chromium; two existing PO tests and two supplier-identifier presence/absence tests.
- Frontend production build and lint passed. Backend Ruff checks and whole-app mypy passed.

Full backend CI, PostgreSQL-specific gates, all browser specs, visual baselines,
and production deployment are not claimed as verified by this task. The code is
a local change for review, not a merged or published release.

AI-assisted: documentation analysis, implementation, and tests were drafted and
executed by the coding agent. No external reference implementation was copied.

## Next slices

The ECC account-cache repair is implemented locally (2026-10-15), with isolated
query/mutation ownership and stale-response rejection. [Evidence and action prompts](plan/ECC-IDENTITY-CACHE-2026-10-15.md).
The follow-on server session/org, calendar and OIDC repairs are also implemented
locally; [combined evidence and action prompts](plan/ECC-SECURITY-2026-10-15.md)
record the final security sequence and validation.

The next review repair, reconciliation source-currency display and CSV retention,
is implemented locally (2026-10-14). [Evidence and action prompts](plan/RECON-CURRENCY-2026-10-14.md)
cover API serialization, honest unknown-unit display and the existing foreign-unit
matching guard. This does not complete the broader bank-reconciliation roadmap.

1. N1 line tax amount / line gross is now implemented locally with separate captured/calculated snapshots, provenance and guarded corrections ([evidence](plan/N1-LINE-AMOUNTS-2026-10-13.md)); pending CI/review.
2. Treat N1 bank-account capture as a separate controlled-data slice: masking, audit/log exclusions, and vendor bank-detail approval controls are prerequisites.
3. N4 page previews are now implemented locally with isolated bounded rendering and tenant-scoped storage/serving ([evidence](plan/N4-CAPTURE-PAGES-2026-10-14.md)); pending CI/review.
4. Keep owner-dependent launch/enterprise tasks in DECISIONS-NEEDED until their prerequisites are supplied.


## L2 received-invoice VAT — local follow-on, 2026-10-15

Implemented the next unblocked rate-breakdown task in detail/review; saved Decimal
line calculations are grouped without changing header totals or historical data.
Missing evidence and header differences remain explicit.
[Contract, action prompt and verification](plan/L2-RECEIVED-VAT-2026-10-15.md):
53 backend checks, six presentation browser checks, an unmocked SQLite-to-Chromium
create/review/correct/refresh check, and lint/type/build/bundle gates passed.


## N1 supplier IBAN — local follow-on, 2026-10-16

Added structured-source payee-account capture and a validated invoice document
snapshot. Guarded capture/review corrections can clear it; detail and audit mask
it, and correction memory excludes it. Vendor-bank approval and payment/SEPA
authority remain independent.
[Contract, per-action prompts, migration and evidence](plan/N1-SUPPLIER-IBAN-2026-10-16.md)
cover 128 backend regressions, 41 overlapping final checks, four PostgreSQL checks,
seven presentation browser checks, and real SQLite API/browser verification.
