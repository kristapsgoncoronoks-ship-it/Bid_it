# Repository Inventory — Module/Stack/Workflow Map

**Repo:** `/home/user/Bid_it` · **Branch:** `claude/bidit-invoice-data-analytics` · **Alembic head:**
`1507ce3eb95f`. Compiled for the Phase 1-11 independent SaaS review board audit
(`docs/audit/`), grounded in the module map independently verified by the Lead System Architect
(`docs/audit/system-architecture.md` §1.1) and cross-checked with fresh directory/dependency reads in
this synthesis pass (commands below).

---

## 1. Stack

| Layer | Technology | Evidence |
|---|---|---|
| Backend framework | FastAPI 0.139.2, async | `backend/requirements.txt` |
| ORM / DB toolkit | SQLAlchemy 2.0.51 (async) + Alembic 1.18.5 | `backend/requirements.txt` |
| Database | Postgres (prod/CI) via `asyncpg` 0.31.0; SQLite (dev/test) via `aiosqlite` 0.22.1 | `backend/requirements.txt`, `docker-compose.yml` |
| Object storage | MinIO (S3-compatible), local-disk `LocalStorage` fallback | `docker-compose.yml` (`minio`/`minio-init` services), `app/core/storage.py` |
| Validation | Pydantic 2.13.4 / pydantic-settings 2.14.2 | `backend/requirements.txt` |
| PDF / document handling | `pypdf`, `pypdfium2`, `reportlab` | `backend/requirements.txt` |
| XML (safe parsing) | `defusedxml` 0.7.1 (all untrusted-XML parse paths) | `backend/requirements.txt`, `docs/audit/security-findings.md` §2.8 |
| Spreadsheet export | `openpyxl` | `backend/requirements.txt` |
| HTTP client | `httpx` | `backend/requirements.txt` |
| Frontend | React 18.3.1 + TypeScript + Vite 6 | `frontend/package.json` |
| E2E testing | Playwright | `frontend/package.json`, used live by the Commercial Director's audit pass |
| Migrations | Alembic, ~66 versioned revision files, single head | `backend/alembic/versions/` (66 files, `alembic heads` → `1507ce3eb95f`) |
| Test count | 138 backend test files; 1091 passed / 4 skipped full-suite baseline (Postgres-gated tests) | `backend/tests/` (138 `.py` files), `docs/audit/test-baseline.md` |
| CI | GitHub Actions: `pii-scan`, `lint`, `backend` (SQLite), `postgres` (Postgres-gated tests), `frontend`, `docker-build`, `deploy` | `.github/workflows/ci.yml` |
| Local/prod-like orchestration | Docker Compose (`db`, `minio`, `minio-init`, `backend`) | `docker-compose.yml` |

## 2. Module map — nine bounded contexts (built code only)

Layered modular monolith: `app/models` → `app/core` → `app/services` → `app/api`, machine-enforced by
`tests/test_boundaries.py` (AST inspection, no code execution — architecture-as-a-test). 48 model files,
83 service files, 41 route-related files (39 route modules + `router.py`/`__init__.py`) as of this audit.

1. **Identity/Tenancy** — models: `organization`, `user`, `membership`, `invitation`, `session`, `sso`;
   core: `authz`, `tenant`; services: `memberships`, `sessions`, `oidc`, `saml`, `scim`; routes: `auth`,
   `sso`, `scim`, `team`, `access`.
2. **AP (payables)** — models: `invoice`, `vendor`, `approval`, `vendor_change_request`; services:
   `validation`, `invoice_workflow`, `ap_payments`, `ap_aging`, `ap_alerts`, `ap_status`,
   `approval_policy`, `duplicates`, `extraction`, `extraction_provider`; routes: `invoices`,
   `invoice_review`, `vendors`.
3. **AR (receivables)** — models: `issued_invoice`, `issuer`, `partner`, `customer`,
   `recurring_invoice`, `dunning_policy`; services: `issued_service`, `issued_lifecycle`,
   `issued_status`, `issued_reports`, `dunning`, `recurring`, `invoice_pdf`, `einvoice`, `facturx`;
   routes: `issued`, `issuer`, `partners`, `customers`, `dunning`, `recurring`.
4. **Expenses** — models: `expense`, `expense_approval`; services: `expenses`, `expense_state`,
   `expense_policy`, `reimbursement`, `receipt_ocr`; routes: `expenses`, `reimbursements`, `receipts`.
5. **Payments/Banking/Settlement** — models: `payment`, `payment_run`, `bank_import`,
   `supplier_payment`; services: `payment_run`, `payments`, `sepa`, `reconciliation`, `cash_position`,
   `cash_flow`, `bank_statement`; routes: `payment_runs`, `reconciliation`.
6. **Money & Compliance kernel** — models: `currency`, `fx`, `tax_code`; core: `money`; services:
   `currencies`, `fx`, `tax_codes`, `vat`; routes: `currencies`, `fx`, `tax_codes`.
7. **Platform floor** — core: `authz`, `security`, `security_headers`, `ratelimit`, `keyvault`,
   `tenant`, `storage`; services: `audit`, `jobs`, `job_handlers`, `scheduler`, `filesec`, `documents`,
   `document_registry`, `document_versions`, `verification`, `queue_health`, `mailer`, `webhooks`,
   `email_intake`; models: `job`, `document`, `document_version`, `webhook`, `email_message`,
   `email_token`, `email_intake`, `audit`, `role_policy`, `retention`, `usage`; routes: `documents`,
   `email`, `jobs`, `platform`, `webhooks`, `retention`, `privacy`, `audit`, `settings`.
8. **Insight/Analytics & Export** (projection layer, reads from the above, owns no independent state) —
   services: `analytics`, `explore`, `dashboard`, `erp_export`, `audit_export`, `report_writers`,
   `benchmark`, `costing`, `budget`, `integrity`; models: `budget`, `costing`; routes: `analytics`
   (Explore/dashboard live under this — see route list below), `budget`, `costing`, `export`,
   `benchmark` (surfaced via `analytics.py`/`dashboard.py`/`export.py` routes).
9. **Transport vertical** — spec-only (`docs/transport/`); confirmed **no code** exists under
   `app/models`, `app/services`, or `app/api` for it (`find app -type d -iname transport` — empty).

## 3. API surface (route modules, `backend/app/api/routes/`)

```
access.py       analytics.py   audit.py        auth.py        billing.py
budget.py       costing.py     currencies.py   customers.py   dashboard.py
documents.py    dunning.py     email.py        expenses.py    export.py
fx.py           integrity.py   invoice_review.py invoices.py  issued.py
issuer.py       jobs.py        modules.py      partners.py    payment_runs.py
platform.py     privacy.py     receipts.py     reconciliation.py recurring.py
reimbursements.py retention.py scim.py         settings.py    sso.py
tax_codes.py    team.py        vendors.py      webhooks.py
```
39 route modules, all mounted through a single `app.api.router.api_router` (verified one-for-one against
the directory listing by the Lead System Architect, `docs/audit/security-findings.md` §2.9). `app/main.py`
adds exactly 3 app-level routes outside that mount: `/health`, `/health/ready`, `/health/queue`.

## 4. Frontend page inventory (`frontend/src/pages/`, 45 pages)

```
AcceptInvite   Access         Audit          Benchmark      Billing
Budget         CaptureQueue   CaptureReview  CashPosition   CostObjects
Currencies     Customers      Dashboard      Documents      DunningSettings
EmailIntake    ExpenseDetail  ExpensePolicy  Expenses       Explore
ForgotPassword Fx             Invoices       InvoiceDetail  Issue
IssuedReports  Issuer         Login          Partners       PaymentRuns
Platform       Receipts       Reconciliation Reimbursements ResetPassword
Review         ReviewInvoice  Sessions       Settings       SsoCallback
TaxCodes       Team           Upload         Vendors        VerifyEmail
```
Grouped by journey: **AP** — Upload, CaptureQueue, CaptureReview, Review, ReviewInvoice, Invoices,
InvoiceDetail, Vendors, PaymentRuns, Reconciliation. **AR** — Issue, IssuedReports, Issuer, Partners,
Customers, DunningSettings. **Expenses** — Expenses, ExpenseDetail, ExpensePolicy, Receipts,
Reimbursements. **Money/Compliance** — Currencies, Fx, TaxCodes, Budget, CostObjects. **Insight** —
Dashboard, CashPosition, Explore, Benchmark, Audit. **Platform/Identity** — Login, AcceptInvite,
ForgotPassword, ResetPassword, VerifyEmail, SsoCallback, Sessions, Team, Access, Settings, Billing,
Platform, Documents, EmailIntake.

## 5. Background jobs / worker tier

`app.models.job.Job` — a durable Postgres/SQLite-backed table queue (`app/services/jobs.py`), no
external broker (Redis/Kafka not used and not evidenced as needed at current scale,
`docs/audit/system-architecture.md` §3 "Architectural notes"). `app/worker.py` runs
`python -m app.worker [--lane KINDS]` for CPU/IO lane isolation. Handlers are `@handler("kind")`
registered (`job_handlers.py`) and dispatched inside the job's tenant scope. `/health/queue` exposes an
SLO probe (`queue_slo_max_pending_age_seconds`, default 900s, 503s on breach).

## 6. Databases / persistence

Single Postgres database in production (SQLite for dev/test), row-level multi-tenancy via 58 registered
`TENANT_MODELS` — enforced three ways: per-query `org_id` filters in app code, an ORM
`do_orm_execute`/`with_loader_criteria` guard, and Postgres Row-Level Security with `FORCE ROW LEVEL
SECURITY` on tenant tables (independently verified live, `docs/audit/security-findings.md` §2.2). Object
storage (documents/receipts/PDFs) via MinIO/S3-compatible storage or `LocalStorage`, content-addressed by
SHA-256 (`app/core/storage.py`).

## 7. External integration seams

- **Email** — inbound intake (`POST /email/inbound`, Mailgun adapter), outbound via `mailer.py`
  (SMTP-gated, outbox-recorded).
- **Identity** — OIDC (with PKCE) + SAML 2.0 + SCIM 2.0 provisioning (`oidc.py`, `saml.py`, `scim.py`).
- **Banking** — SEPA pain.001 export (`sepa.py`), CAMT.053 bank-statement import (`bank_statement.py`).
- **Billing** — pluggable provider seam (`billing_provider.py`), `NullProvider` default (no real
  payment collection out of the box — see `docs/audit/commercial-readiness.md` §7).
- **FX** — ECB rate feed (`fx.py`), XML parsed via `defusedxml`.
- **Webhooks** — outbound delivery with SSRF guard (`webhooks.py::assert_public_url`).
- **E-invoicing** — EN-16931/UBL/CII structured-XML and Factur-X/ZUGFeRD hybrid-PDF parsing
  (`einvoice.py`, `facturx.py`).
- **ERP export** — accounting-ledger CSV/export hub (`erp_export.py`).

---

*Companion document: `docs/audit/data-flows.md` (per-workflow request/data-flow detail for AP, AR, and
expenses). Full architecture narrative: `docs/audit/system-architecture.md` §1.*
