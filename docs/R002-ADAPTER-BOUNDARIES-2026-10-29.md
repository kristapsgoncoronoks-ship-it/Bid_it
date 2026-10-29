# ACC02/ACC03 unavailable adapter boundary

Baseline: main `90891d8d52cf5aefc4467e3fa7f6070c042acec2`.
Assignments: R002-A-ACC02-P01 r2 / R002-A-ACC03-P01 r2; R002-RB06/RB07.

## Behaviour and purpose

Xero and QuickBooks remain unavailable. GET `/export/formats` now supplies the
label, action ID, reason and setup steps; the same service catalogue refuses
unconfigured render calls with `export_adapter_unconfigured` before reading rows.
Legacy hard-coded branded renderers were removed. Repository call search found
only accounting_preview calling `render("generic", rows)` in production.

Each disabled adapter displays its server-provided reason outside the generic
preview panel, with a unique accessible description and keyboard-operable setup
requirements disclosure. There is no export command behind the disabled control.
The query is scoped to AuthContext.cacheIdentity, abortable, bounded at 15 seconds,
without automatic retries or polling. Invalid, missing, duplicate or unsupported
enabled-provider descriptors fail closed; the generic preview remains independent.
Authorization errors never expose server detail. A manual retry only reads metadata.

## Financial / tenant / schema impact

No migration, profile persistence, dependency or financial write is introduced.
Existing EXPORT_RUN authorization and generic frozen-preview money/tenant/audit
contracts are preserved. Route and service use the same configuration guard.
The additive OpenAPI change types the existing format endpoint and adds action_id
and setup_steps; existing key/enabled/contract/reason/label fields remain. Branded
labels now include their unavailable state. The generated snapshot requires human
review, particularly for client consumers relying on exact label text.

## Validation

Local focused backend suites: 39 passed (ERP export, accounting preview,
architecture boundaries and route authorization). Ruff and mypy passed; frontend
lint/type/build and upload-proxy tests passed. First-load bundle: 434.8 KiB raw /
128.4 KiB gzip, within 460/135 limits. This is absolute output, not an improvement
claim. Full backend and browser results are recorded in the PR once available.

Browser cases distinguish mocked UI failure evidence from actual provider
completion. They cover action-specific descriptions, keyboard disclosure, zero
export command from setup, read retry, 403/429/500/transport failure and malformed
catalogues. No provider import or financial completion is claimed. No PostgreSQL
schema/concurrency behaviour changes; hosted PostgreSQL remains an applicable gate.

No CPU/RSS/latency or full-stack hardware trial is claimed. Provider imports,
OCR/PDF workloads, 2-vCPU/4-GiB headroom and live deployment are unverified.

## Before enablement

Select one pilot entity/jurisdiction/accountant/provider product and region.
Version the exact template, accounts/taxes/tracking/currencies, dates and amount
basis; for QuickBooks also confirm edition eligibility, supplier references,
batch limits and unsupported credits/discounts. Use synthetic sandbox bills and
independently reconcile all lines/taxes/currencies before any enablement PR.
Do not treat Xero NONE as a universal tax default. Preserve immutable source
versions, Decimal/string amounts, durable command/artifact receipts and atomic
audit/outbox intent for future commands. No profile database is justified yet.

References (checked 29 October 2026):
- https://react.dev/reference/react/useId
- https://playwright.dev/docs/test-assertions
- https://developer.xero.com/documentation/api/accounting/invoices/
- https://central.xero.com/s/article/Import-customer-or-supplier-invoices
- https://quickbooks.intuit.com/learn-support/en-us/help-article/import-export-data-files/import-bills-quickbooks-online/L7OAF3yLT_US_en_US

## Rollback / review

UI regressions may be reverted independently, but never restore production-callable
unreviewed branded renderers. Public branded routes must remain 409, and failure
must remain fail-closed. New dependencies/tenant exemptions are unnecessary.
Human review must confirm descriptor/API compatibility and blocker accessibility;
CI and provider pilot evidence remain separate from author assertions.

AI-assisted: implementation, regression tests and documentation were drafted by a
coding agent. No external source implementation was copied. Human review pending.

## Current development integration follow-up

The original main-based implementation above is preserved as PR #96's evidence,
not current combined-head qualification. It is now composed with qualified
planning/finance/security development, retaining current responsive wrappers,
typed organization fixtures and all 417 API paths. The new catalogue query marks
its errors as handled so its safe inline message cannot coexist with raw global
error toasts; stable format/action IDs are checked before rendering. Secondary
blocker/setup copy uses existing readable 14px tokens. Both adapters stay disabled.

[Combined source, actual test results, preserved attempts and remaining limits](reviews/ACCOUNTING-ADAPTER-INTEGRATION-2026-10-29.md).
Generic CSV, global error handling, provider selection and production ownership
are unchanged. Full-stack provider/identity acceptance and human review remain
separate from blocked-state browser checks.
