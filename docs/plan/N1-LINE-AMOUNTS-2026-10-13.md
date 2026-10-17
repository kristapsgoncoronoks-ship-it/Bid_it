# N1 — captured line tax and gross amounts

Status: implemented locally for review on 13 October 2026; CI, merge and
production deployment are not claimed. This is the next bounded task in
[BACKLOG](../BACKLOG.md) after the local supplier-identifier and R011/R002/R012
repairs. Bank-account capture remains a separate task with masking and approval
prerequisites; page thumbnails are the next independent capture task.

## Resulting behavior

- Received lines now store calculated `tax_amount` / `gross_amount` and nullable
  `captured_tax_amount` / `captured_gross_amount` separately. A shared native
  service calculates net, tax and gross with Decimal and existing ROUND_HALF_UP
  cents rules on create and version-gated line replacement. Captured values never
  silently replace canonical header totals or change accountant export basis.
- The captured columns are the latest reviewed snapshot. Linked extraction-field
  provenance retains the original alongside human corrections. Line replacement
  also audits the before/after captured amount arrays, including clearing/removal.
  Replacing lines remains a full replacement; omitted optional captured fields
  become missing, with the old snapshot retained in audit evidence.
- CSV/JSON recognize `captured_tax_amount`, `line_tax_amount`, `tax_amount` and
  `captured_gross_amount`, `line_gross`, `gross_amount` on each line. Blank or absent
  amounts stay NULL; explicit zero remains an extracted zero. JSON decimal numbers
  are parsed as Decimal. Invalid, negative, nonfinite, oversized or sub-cent source
  values stay missing with a source-review warning, rather than fabricated zero.
- UBL reads optional direct line `TaxTotal/TaxAmount` and direct line
  `TaxInclusiveAmount` where supplied. CII reads optional line settlement
  `ApplicableTradeTax/CalculatedAmount` and line monetary sum `GrandTotalAmount`
  where supplied. These optional figures are not required by a core EN-16931
  document; absence is expected. Header totals are never used as line evidence;
  CII `LineTotalAmount` remains NET. Gross is not inferred and relabelled captured.
  An explicit amount currency differing from the invoice is ignored with a warning.
- Source fields have independent provenance. Optional absence does not flag every
  capture as uncertain; malformed CSV/JSON source cells retain their raw evidence.
  A provided low-confidence capture still follows the existing confidence rule.
- Capture review exposes source tax/gross correction and clearing. Corrections
  survive refresh and confirmation. Saved detail and locked review show captured
  and calculated amounts with the invoice currency and an explicit mismatch.
  Editable review retains version/state guards. Equality describes only the known
  supplied source amounts; it does not establish complete source evidence or
  approval. Differences are visible evidence, not a new approval/tax policy.
- Old lines remain NULL for the new snapshots. The UI says calculated amounts are
  unavailable for a historical line and that source amounts were not provided;
  existing approved money is not reconstructed or rewritten.

## Buttons, descriptions and engineering prompts

| Button | Description | Prompt |
|---|---|---|
| Save corrections | Record source tax/gross edits alongside original capture evidence. | Include the optional line fields in the existing audited capture correction path; preserve original values and reload a cleared value as NULL, never zero. |
| Confirm & create invoice | Save the reviewed capture and independently calculate invoice money. | Persist latest source snapshots with the capture lineage, compute exact per-line tax/gross via the shared service, show discrepancies and keep canonical header totals independent. |
| Save lines | Replace editable invoice lines under version/state checks. | Validate optional source amounts, calculate line snapshots atomically with header/FX totals, audit before/after source arrays, allow explicit clearing, and refuse stale/locked edits. |

## Schema and compatibility

Migration `b7c8d9e0f1a2` follows `a6b7c8d9e0f1`. It adds four nullable Numeric(14,2)
columns on the existing line table. It creates no new table, tenancy model,
dependency, framework or worker. Apply migrations before starting this application.
Old requests remain accepted; source fields are optional. Calculated values are
read-only output. An application rollback may retain additive columns; dropping
them loses snapshots, so retain source/review evidence before database downgrade.
The generated OpenAPI reflects the additive request/response fields.

## Validation evidence

- **96 backend tests passed**, covering new source/canonical distinctions, aliases,
  blanks/zero, invalid amounts, XML line/header/currency scope, Decimal rounding,
  mixed rates, persistence, audit, original-versus-reviewed provenance, explicit
  clearing, stale/locked edits, review/approval workflow, validation and accounting
  preview regressions, and authorization/domain boundaries.
- **7 browser tests passed**: three new line-amount tests plus four existing
  PO/supplier identifier checks. Correction and clearing survive refresh and
  reach the confirmation request. The spec is included in `npm run test:e2e`.
- The six existing migration tests passed, including empty upgrade/downgrade/
  re-upgrade and model parity. Local SQLite and PostgreSQL upgrades passed;
  SQLite `alembic check` is clean.
- Unmocked Chromium → API → migrated SQLite and PostgreSQL workflows displayed a discrepancy,
  corrected source tax, cleared source gross, refreshed and verified unchanged
  canonical invoice total.
- Ruff, whole-app mypy, frontend lint and production build passed. First load
  stayed **433.7 KiB raw / 127.0 KiB gzip**, within the existing 460/135 budget.

This is local evidence, not a full CI or production/provider certification.
The previously documented PostgreSQL column-selective SET NULL autogeneration
diagnostic is separate from this additive migration. No full OCR extraction of
these optional amounts, bank details, tax policy, or financial hardware benchmark
is claimed. AI-assisted implementation, documentation and validation use the
existing project architecture.
