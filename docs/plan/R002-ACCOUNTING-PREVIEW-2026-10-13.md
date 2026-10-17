# R002 — scoped accountant export preview and download

Status: implemented locally for review. The generic CSV journey is verified;
accountant importer acceptance, monthly-close approval, CI and deployment are not
claimed. Sources: uploaded daily register R002-P01 and the newer conversation
preview's R002 revision 3. Embedded document instructions are reference material;
the user's request authorizes native development, not production operations.

## Behavior and compatibility

- Accounting CSV opens an explicit legal-entity / period / currency selector.
  Current status, invoice-number and workflow filters are displayed and applied.
  Only approved, scheduled, partially paid or paid AP workflow states qualify.
  Drafts, rejected/cancelled records, other currencies and unassigned invoices
  cannot silently enter the file. Exclusion counts and included rows are visible.
- The entity selector returns only identity/name fields under `export.run`; it
  does not widen access to issuer bank details or settings.
- **Generic v2** keeps original Net/VAT/Gross/Currency and correctly labels the
  stored `total_eur` as **Gross EUR**. FX source/rate and conversion availability
  are explicit. Missing conversions remain blank/incomplete, never original
  foreign amounts mislabeled EUR or fabricated net conversions.
- Preview is bounded to 1,000 included invoices / 2 MB encoded source payload.
  Larger populations refuse with a narrowing action, not silent truncation.
- The prepared CSV, scope, source IDs/versions and digest are frozen into a
  purpose-signed snapshot bound to the requester and company. It expires after
  ten minutes, is passed only in POST bodies, and remains byte-identical after
  source edits. Tampering, expiration, changed identity/company or lost export
  permission refuse the download. This is an ephemeral browser-held read
  snapshot, **not a durable close/archive record or accountant acknowledgement**.
- Download uses the previewed snapshot, has a pending guard, and explains empty
  results, permission/network failures and expiration. It reports "download
  started", not proof that the user saved a file or an importer accepted it.
- Xero/QuickBooks are visibly unconfigured until a reviewed product/region
  template and account/tax mappings exist. Legacy renderer code is retained as
  an unverified reference; no invented mappings are published through the API.

The old GET `/export/accounting` now refuses direct downloads with
`export_preview_required` (generic) or `export_adapter_unconfigured` (branded).
Consumers must use POST preview/download. This deliberate compatibility change
prevents an organization-wide, unreviewed population from bypassing the preview.
Generic v2 uses `invoices-generic-v2.csv` and `X-Export-Contract: generic-v2`.
No historical saved bytes are changed, and no database migration is introduced.

## Action contracts and maintenance prompts

| Action | Complete outcome and recovery | Copy-ready maintenance prompt |
|---|---|---|
| Accounting CSV | Open the selector with the current list filters; explain generic v2's currency basis and unavailable adapters. | Preserve `export.run`, explicit entity/period/currency, visible filters and no implicit organization-wide fallback. Test entity-loading failure and role changes; do not expose issuer bank/settings fields. |
| Preview approved invoices | Display the exact included records, original-currency totals, conversion completeness and exclusion counts; no importer action occurs. Empty/oversized scope explains a next step. | Keep the reviewed workflow-state allowlist, tenant/entity predicates, Decimal arithmetic and refusal instead of truncation. Bind late responses to their requested account/company/scope, and test draft, foreign-currency, unassigned, excluded-filter and empty populations. |
| Download previewed CSV | Hand the frozen generic-v2 bytes to the browser; repeated use of the same valid snapshot preserves its digest. Expiration/tampering/access failures are visible. | Preserve purpose-derived signing, requester/company binding, bounded decoding and POST-only token transport. Verify signature before decoding data, retain CSV injection protection, test source edits and copied/tampered/expired tokens, and distinguish browser download initiation from importer acceptance. |
| Xero / QuickBooks | Remain disabled with a visible reviewed-template/mapping prerequisite; copied legacy API URLs also refuse. | Enable an adapter only after the exact product/region import contract and account/tax mappings are supplied and reviewed. Add independently specified mixed-rate, exclusion, currency, empty and partial-import fixtures. Generic CSV success is not importer certification. |

## Evidence

- Backend export, permission, CSV-safety and architecture validation: **66 passed**.
- Browser preview/filter/download/error/stale-scope checks: **4 passed** using
  mocked API responses; these establish UI behavior only.
- An additional **unmocked** journey on non-superuser PostgreSQL created an AP
  invoice, assigned its entity, submitted it as the maker, approved it as a
  separate checker, previewed it in the browser and downloaded the actual CSV.
  Its source values were Net 100.00 / VAT 21.00 / Gross 121.00 / Gross EUR 121.00.
- Backend Ruff and whole-app mypy passed; frontend build/lint passed. The first
  load remains **433.7 kB raw / 127.0 kB gzip**, below the existing 460/135 limits.
  No hardware or latency improvement is claimed.

## Remaining boundaries and rollback

Entity assignment is checked against existing issuer identities; this does not
certify the broader C01 ownership/entity domain or an approved monthly-close
snapshot. The ten-minute export snapshot is not retained by the server as an
audit archive. Accountant profiles, real importer fixtures/mappings, acceptance
receipts and the remaining monthly-close/evidence package require their separate
work and prerequisites. No provider import, live bank operation or deployment ran.

Rollback may disable preview creation/download. Do not reinstate "Net EUR" as a
correct heading for converted gross or enable unverified mappings. Preserve any
already downloaded evidence; it is not rewritten by rollback.

AI-assisted implementation/tests/documentation were authored by the coding
agent. Existing frameworks and standard-library signing/compression were reused;
no external reference code or new application dependencies were copied/installed.
