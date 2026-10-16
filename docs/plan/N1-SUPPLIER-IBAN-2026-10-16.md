# N1 — Captured supplier IBAN (2026-10-16)

Implemented locally, pending review/CI; not deployed. Continues the remaining N1
received-invoice capture task. PRD §9 requires: "never log secrets/IBANs; mask bank
data in analytics." Existing controlled vendor-bank changes remain authoritative
for payment runs; a document instruction is not an approved account change.

## Behavior and boundaries

- `invoices.supplier_iban` is a nullable document snapshot. Existing invoices stay
  null; there is no inference from vendor master data or historical documents.
- JSON/CSV accept `supplier_iban`, `supplier_bank_iban` and `payee_iban` (case and
  surrounding header whitespace normalized). Generic `iban` and buyer fields are
  ignored because their owner is ambiguous. CSV checks all rows for conflicts.
- UBL reads direct header `PaymentMeans/PayeeFinancialAccount/ID` (BT-84); CII reads
  header settlement payment means' `PayeePartyCreditorFinancialAccount/IBANID`.
  Payer/mandate and line-level accounts are not substituted. Multiple distinct
  accounts are left unresolved; duplicate spellings of one account are normalized.
- Uses the existing bank identifier validator: normalized whitespace/case,
  supported country lengths and MOD-97 checksum. Invalid/unsupported source input
  stays missing with a non-echoing warning, rather than failing the entire intake.
  An invalid direct create/review correction returns a non-echoing 422. Non-text
  input is rejected. Structural validity does not establish account ownership.
- Capture provenance contains the normalized captured value, with honest missing
  status; corrections and clearing retain it alongside reviewed values. The
  original source file remains available. No OCR bank-number inference was added.
- Authorized capture and invoice review responses carry the full field so a human
  can correct it. Detail exposes only `supplier_iban_masked` (last four + length),
  and the ordinary invoice list carries neither field. Existing reporting,
  accountant export and webhook payloads do not gain a bank field.
- This is not an encryption-at-rest change. Document drafts/provenance and the
  invoice snapshot remain within the existing tenant-scoped storage/access model,
  like the original document and vendor bank details. Owner workspace portability
  exports remain private full-data exports, not masked analytics.
- Capture-correction audit entries mask old/new values. Header edits audit changed
  field names only. Bank inputs are excluded from model repr and from correction
  memory observation, writes and hints, preventing reuse across later documents.
- Existing expected-identity, permission, tenant, version and workflow locks apply.
  A failed correction batch rolls back all fields and memory changes. No invoice
  capture/edit writes Vendor.iban, approves a vendor change or supplies a payment
  run/SEPA account. Detail and review explain this separation visibly.

Non-IBAN local account numbers, bank ownership verification and BIC capture are
outside this slice; unsupported account instructions require source review and
must use the existing controlled vendor workflow before payment.

## Existing actions and regression prompts

| Action | Outcome / recovery | Development regression prompt |
| --- | --- | --- |
| Save corrections | Validates and records the reviewed account, masks audit values and keeps the original. Invalid input remains editable with visible error; clear the field if unresolved. | Upload qualified JSON/CSV and UBL/CII payee accounts. Correct, refresh and clear the IBAN; preserve original evidence. Reject invalid/ambiguous values without echoing them into warnings, audit or logs. Prove a mixed invalid batch has no partial effects and no bank suggestion is learned. |
| Confirm & create invoice | Saves the reviewed snapshot alongside the chosen vendor; follows the existing confirmation and duplicate checks. | Correct or clear a capture, confirm, and assert the exact resulting snapshot, masked detail, and unchanged vendor bank/account approval state. |
| Save header | Uses the reviewed invoice version and editable workflow; updates/clears only the document snapshot. | Test invalid-input recovery, normalized correction, refresh and clearing. Refuse stale versions, locked invoices and foreign-tenant IDs without changing either snapshot or vendor payment authority. |

No new payment button or account-approval shortcut is introduced.

## Migration and rollback

Migration `e0f1a2b3c4d5` follows `d9e0f1a2b3c4` and adds only nullable
`invoices.supplier_iban VARCHAR(34)`. No backfill, index, money or tenant changes.
SQLite and PostgreSQL upgraded successfully; SQLite `alembic check` found no drift.
Existing migration tests also passed. The column inherits invoices' tenant policy.

For application rollback, leave the additive nullable column in place. A schema
downgrade drops captured bank evidence: export it securely first if required.
OpenAPI and generated ER diagrams were refreshed; the README count comes from
3,591 collected backend tests, 143 revisions and 116 service modules.

## Verification

- 128 backend regressions passed: supplier capture/provenance, memory, review,
  vendor-bank change approval, payment runs, SEPA, migrations and derived docs.
- After adding a mixed invalid correction-batch check, 41 focused capture/security
  boundary/tenant checks passed. This overlaps the broader group; do not add counts.
  The feature contributes 29 default-database cases plus one PostgreSQL case.
- Four PostgreSQL tests passed using a non-superuser, non-BYPASSRLS role: real
  captured snapshot create/correct/clear with unchanged vendor bank/approval,
  session isolation and offboarding/SSO tenant controls. The new bank PG case was
  added to the existing PG CI job.
- Seven Playwright presentation tests passed: four bank workflows and three N1
  line-evidence regressions. Their API responses are controlled fixtures.
- Additional unmocked SQLite API → Chromium verification passed for masked detail,
  invalid-input recovery, normalized saved correction, clearing and reload, with
  the vendor bank and provisional approval status unchanged. Synthetic public
  IBAN registry examples only. One repeat navigation timed out before the final
  successful run; browser checks are not a production reliability certification.
- Ruff/format checks, mypy for all 413 app modules, frontend lint/types/build and
  unchanged bundle gate passed: 435.7 kB raw / 127.6 kB gzip (limits 460 / 135).
- No full backend suite or production/real bank/identity-provider run was performed
  for this slice. The standard local API and frontend use the current source.
