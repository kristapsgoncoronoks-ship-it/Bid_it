# Sample prepared forms (document templates)

Ready-to-use `.docx` templates for the Document Management module. They are **sample
assets**, not auto-loaded: an admin **uploads** one through the `/customers` page
("Document templates" form, the `add_template` action) to make it available for
generating a customer's prepared documents. Nothing here is read at runtime until you
upload it.

Once uploaded, a template is filled per customer (and, for a power of attorney, per refund
country) when you generate a document request. Placeholders use `{{field}}` syntax and are
substituted from the customer's master data, tolerant of placeholders Word has split across
formatting runs (each form ships at least one deliberately split placeholder to exercise
that path).

## The forms

| File | Kind | Purpose |
|------|------|---------|
| `poa_generic.docx` | `power_of_attorney` | Power of Attorney authorising the agency to file VAT refunds for the client in a refund country before that country's tax authority. A *received* signed PoA satisfies the country activation checklist. |
| `poa_DE.docx` | `power_of_attorney` | Germany-specific bilingual (DE/EN) Power of Attorney variant. |
| `engagement_contract.docx` | `contract` | Service / engagement contract: scope, service fee, payout route, bank details and the supplier accounts in scope. |

`.docx` templates are delivered prefilled. The app converts a prefilled `.docx` to PDF via
LibreOffice (`soffice`) when available; where `soffice` is unavailable it gracefully falls
back to delivering the prefilled `.docx`.

## Available `{{field}}` placeholders

These mirror `customer_master.template_fields()` (and the values
`customer_master.merge_fields()` emits). Every value is substituted as text.

- `{{company_name}}` — legal company name
- `{{code}}` — internal customer code
- `{{reg_number}}` — company registration number
- `{{vat_number}}` — VAT identification number
- `{{legal_address}}` — registered office address
- `{{country}}` — customer's home country
- `{{nace_code}}` — NACE business activity code
- `{{home_portal}}` — home tax portal
- `{{phone}}` — phone
- `{{email}}` — email
- `{{status}}` — customer status
- `{{notes}}` — free-text notes
- `{{payout_route}}` — refund payout route (`customer` or `us`)
- `{{signatory_name}}` — authorised signatory name
- `{{signatory_title}}` — authorised signatory title
- `{{bank_iban}}` — refund payout IBAN
- `{{bank_swift}}` — refund payout SWIFT/BIC
- `{{bank_name}}` — refund payout bank name
- `{{refund_country}}` — the refund country (set per power-of-attorney request)
- `{{tax_authority}}` — that refund country's tax authority
- `{{fee_pct}}` — service fee percentage (numeric)
- `{{fee_min}}` — minimum fee per declaration (EUR)
- `{{fee_pct_fmt}}` — service fee percentage, formatted (e.g. `12%`)
- `{{supplier_accounts}}` — one `supplier: account_no` per line
- `{{today}}` — today's date, ISO (`YYYY-MM-DD`)
- `{{today_fmt}}` — today's date, long form (e.g. `14 June 2026`)

Prices and amounts are NET EUR (VAT excluded). An unfilled placeholder means the field is
empty on the customer's master record — fill it in on the customer's page and re-generate.
