# SAF-T export — programmable OECD-core generator (prework)

`saft.py` builds an **OECD-SAF-T core structure** (the AuditFile skeleton common to
the national variants) parameterised by a **CountryProfile**, so any jurisdiction can
be specialised later without rewriting the generator.

> The output is a **CORE STRUCTURE, not a validated submission** for any specific tax
> authority. Every file carries this caveat as a top-level XML comment and a
> `Header/HeaderComment` element (`saft.CORE_BANNER`). To file for a country you plug
> in that country's profile (namespace/version/required fields) AND validate against
> the authority's published XSD.

Read-only over the engine-owned product DB. It reuses `queries.q_ledger` — the SAME
ledger math the accounting CSV uses — so the XML totals **reconcile** with
`queries.q_expense`. Basis: **NET EUR, final (rebates applied); gross = net + VAT.**
Stdlib only (`xml.etree.ElementTree`; ET handles XML escaping). No new dependency.

## Entry point

`saft.build_saft(period=None, entity=None, profile=None) -> (filename, xml_bytes)`
- `period` defaults to the latest loaded period; `entity` (a company name) optionally
  restricts the file; `profile` defaults to `DEFAULT_PROFILE`.
- Raises `ValueError("no data loaded - nothing to export")` when there is no
  data/period (or no rows for the entity/period).
- Web: `GET /export/saft?period=&entity=&profile=` (gated `exports` / analytics
  module; not admin-only). A button sits on `/expenses` next to the accounting CSV.

## Data -> SAF-T element mapping (core)

| SAF-T element | Source |
|---|---|
| `Header/AuditFileVersion` | `profile.audit_file_version` |
| `Header/AuditFileCountry` | entity country (`customer_master`), else "" |
| `Header/AuditFileDateCreated` | today (ISO) |
| `Header/SoftwareCompanyName`, `SoftwareID`, `ProductID` | this platform |
| `Header/Company/RegistrationNumber` | `customer_master.reg_number` (entity) / `INPUT` |
| `Header/Company/Name` | `customer_master.company_name` / `(all entities)` |
| `Header/Company/TaxRegistration/TaxRegistrationNumber` | `customer_master.vat_number` |
| `Header/Company/Address/Country` | `customer_master.country` |
| `Header/DefaultCurrencyCode` | `profile.currency` |
| `Header/SelectionCriteria/Period*`/`Selection*Date` | min/max ledger `date` for the period |
| `MasterFiles/Suppliers/Supplier` | one per distinct ledger `supplier` (code/name) |
| ` …/TaxRegistration/TaxRegistrationNumber` | `supplier_master.get_issuer` VAT id |
| `MasterFiles/TaxTable/TaxTableEntry` | one per distinct ledger `vat_rate_pct` |
| `GeneralLedgerEntries/NumberOfEntries` | row count emitted |
| `GeneralLedgerEntries/TotalDebit` / `TotalCredit` | SUM(net_eur) / SUM(vat_eur) |
| `…/Journal/Transaction` | one per ledger ROW |
| `Transaction/TransactionDate` | ledger `date` |
| `Transaction/Description` | `note` / `product` / `product_group` |
| `Transaction/SupplierID`, `Currency` | ledger `supplier`, `currency` |
| `…/DebitLine/DebitAmount` + `TaxInformation/TaxAmount` | `net_eur`, `vat_eur` |
| `Transaction/NetAmount`/`TaxAmount`/`GrossAmount` | `net_eur`, `vat_eur`, `net+vat` |

SelectionCriteria period bounds come from the ACTUAL ledger date range (a fuelling can
fall just outside the label month) rather than parsing the period string.

The transactions section is `GeneralLedgerEntries` (the core picks GL over
`SourceDocuments`); **one `Transaction` per ledger row** — never an aggregate.

## CountryProfile seam — adding a real country

```python
@dataclass(frozen=True)
class CountryProfile:
    code, namespace, schema_version, audit_file_version,
    file_name_pattern="SAFT_{code}_{entity}_{period}.xml",
    sections=("Header","MasterFiles","GeneralLedgerEntries"),
    currency="EUR"
```

`DEFAULT_PROFILE` ("OECD") is **generic** — placeholder namespace
(`urn:oecd:ces:std:saf-t:core:generic`) and version, clearly marked. Register real
profiles in `PROFILES`; `get_profile(code)` resolves them (unknown -> DEFAULT_PROFILE,
warned, not raised). `sections` lets a profile drop blocks it doesn't need.

## What's still needed for a VALID submission per country

A profile only abstracts namespace/version/file-name/sections. For a real filing each
jurisdiction also needs the **correct XSD target namespace + schema version, its
required elements and ordering, and validation against the authority's published
schema**. Examples of the work that remains:

- **Lithuania (SAF-T / i.SAF-T, v2.01):** VMI namespace/version, the LT-required
  MasterFiles/GLEntries shape; validate vs the VMI XSD.
- **Poland (JPK / JPK_VAT):** a different schema family — the element set and XSD
  differ entirely; a profile abstracts only namespace/version/sections.
- **Portugal (SAF-T PT):** the original SAF-T; `AuditFileVersion` and required
  Header/MasterFiles fields per Portaria; validate vs the AT XSD.

This module gives you the reconciled core; the profile + XSD validation make it real.

## Reconciliation guarantee

Every ledger row becomes exactly one GL `Transaction`; **no row is silently dropped**
(a single malformed row is skipped AND logged via `applog.get("saft")`). Each row's
amounts are `money.f2`-quantized (the SAME per-line quantization as `q_ledger`), so
the XML's `TotalDebit`/`TotalCredit` (SUM net / SUM VAT) tie to
`queries.q_expense(period).totals` within per-line rounding (`|diff| <= 0.01 * n`),
the same tolerance the accounting-CSV reconciliation asserts. If the totals ever fail
to tie, that is a sign the entries diverge from the books — stop, don't ship.
