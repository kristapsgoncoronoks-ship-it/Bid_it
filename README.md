# InvoiceIQ — the SME financial workspace

A **multi-tenant SaaS financial workspace for SMEs and accountancy practices**:
supplier-invoice capture and approval (AP), customer invoicing (AR), payments and
settlement, expenses, analytics, exports, and organization/identity administration.
A transport vertical (EU cross-border VAT refunds, Dir. 2008/9/EC) is planned as a
plug-in bounded context ([ADR-0023](./docs/architecture/adr/0023-platform-evolution-and-transport-seam.md)).

**Stack:** FastAPI + async SQLAlchemy 2.0 + Alembic on PostgreSQL (SQLite for
zero-setup dev/test) · React 18 + Vite + TypeScript + Tailwind SPA · Docker.

**Scale of the codebase (verified against this tree):** 64 database tables
(64 Alembic revisions, single head), 47 model modules, 79 service modules,
38 route modules, 39 SPA pages, ~980 collected backend tests, 7 CI jobs.

> **The specification lives in [`docs/`](./docs), not here.**
> [`docs/architecture/adr/`](./docs/architecture/adr/README.md) (27 ADRs) and
> [`docs/product/`](./docs/product) are authoritative; start with
> [`docs/architecture/overview.md`](./docs/architecture/overview.md). This README
> is only the front door.

## What it does

- **AP — supplier invoices**: multi-channel capture (upload, email-in, API) with
  deterministic-first extraction (UBL/CII XML, Factur-X/ZUGFeRD hybrid PDFs, PDF
  text layer, Tesseract OCR, CSV/JSON), an async parse queue, per-field
  provenance/confidence, a human review queue, a 14-state approval workflow with
  priority-ordered approval policies and segregation of duties, and **one**
  validation engine with explicit per-rule `block | advise` policies (ADR-0026).
- **Vendor master under dual control**: changing a vendor's IBAN/tax id is a
  **pending change request a second approver applies** (maker ≠ checker), with
  IBAN mod-97 + BIC validation at every write path and again in the SEPA builder
  (ADR-0025).
- **AR — customer invoicing**: multi-entity issuer registry with per-entity
  gap-free numbering, Art. 226 completeness gate, seller/buyer snapshots at
  issue, credit notes, cancel/void/write-off as distinct events, server-side VAT
  (4 schemes), PDF + EN 16931 CII XML, recurring schedules, dunning ladder,
  partner document gates, penalty invoicing, cash application on an append-only
  ledger.
- **Payments & settlement**: payment runs with **maker ≠ checker ≠ payer**,
  export-once-guarded SEPA pain.001 with a unique `MsgId` per generation and
  surfaced skipped payees; AP/AR settlement ledgers where **status is derived,
  never stored**; bank statement import (CSV/camt.053/PDF) and advisory
  reconciliation.
- **Expenses**: reports with standard/mileage/per-diem items, receipt capture,
  bank-statement inbox, an 11-rule policy engine, approval chains, reimbursement
  batches (CSV + SEPA).
- **Money correctness**: `Decimal` ROUND_HALF_UP everywhere, server-recomputed
  totals, **one FX convention** (ECB units-per-EUR, divide; `fx_source` a closed
  enum; `unknown` → NULL, never a guess; no cross-currency sums without a
  recorded conversion) — ADR-0010/0026.
- **Analytics & exports**: KPI dashboards, a self-service Explore pivot,
  supplier benchmark, budget, AR reports, FX-vs-ECB markup; CSV exports
  (formula-injection-safe), accounting-ledger, Xero/QuickBooks bill exports,
  audit export.
- **Organization & identity**: multi-org membership with org switching,
  invitations, four stored role tiers resolving to an 8-role × 20-permission
  deny-by-default matrix enforced **structurally on every router** with
  both-direction CI coverage (ADR-0024); per-request org-status enforcement with
  session revocation; OIDC SSO (PKCE S256), SCIM 2.0 Users, retention + legal
  hold, GDPR erasure, data-residency pinning, plans/entitlements/usage metering,
  and Stripe/EveryPay billing behind a provider seam (code-complete, not live).
- **Tenant isolation, three layers, tested**: per-query `org_id` filters + an ORM
  `do_orm_execute` guard over a 58-model registry + Postgres `FORCE ROW LEVEL
  SECURITY`; CI asserts RLS/model set-equality (`tests/test_rls.py`), behavioural
  isolation per table over the real query path (`tests/test_tenancy_parity.py`),
  and opaque 404s on cross-tenant ids.
- **Audit**: every mutating operation writes a hash-chained, append-only,
  per-tenant-sequenced audit event in the same transaction (ADR-0012).
- **AI policy before any AI**: with default settings the system runs end to end
  with **zero external calls** — CI-enforced with the network blocked at the
  socket layer (`tests/test_ai_policy.py`, ADR-0027).

## Quick start

```bash
# Docker (Postgres + API + SPA)
docker compose up --build      # web: :8080  api: :8000/docs
docker compose exec backend python -m app.seed   # demo login: demo@invoiceiq.app / demo1234

# Local, no Docker (backend defaults to SQLite)
make install                   # backend venv + frontend deps
cd backend && . .venv/bin/activate && python -m app.seed && uvicorn app.main:app --reload
cd frontend && npm run dev     # http://localhost:5173
```

## Commands (from the Makefile)

```bash
make test         # backend: python -m pytest -q
make lint         # ruff check + ruff format --check
make fmt          # ruff auto-fix + format
make typecheck    # mypy app/core   (CI runs the stricter `mypy app`)
make check        # lint + typecheck + test
make migrate      # alembic upgrade head
make migration m="add x"       # autogenerate a revision (then READ + EDIT it)
make openapi      # regenerate backend/openapi.json
make build        # frontend: tsc --noEmit && vite build
make up / down / logs          # docker compose
```

**CI is authoritative and stricter than the Makefile shortcuts.** The 7 jobs in
[.github/workflows/ci.yml](./.github/workflows/ci.yml): `pii-scan` (WO-6
quarantine gate), `lint`, `backend` (SQLite suite), `postgres` (real-Postgres RLS
+ concurrency, `NOSUPERUSER` role), `frontend` (typecheck + build), `docker-build`,
`deploy`. Reproduce locally:

```bash
cd backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests
mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic upgrade head && alembic check
python -m pytest -q
cd ../frontend && npm run build
```

## Engineering rules (the short version)

- Layering `models → core → services → api` is machine-enforced
  (`tests/test_boundaries.py`). **All business logic in services**; routes are
  thin controllers.
- Every route declares its permission structurally or sits on the reasoned
  `PUBLIC_ROUTES` allow-list — CI asserts coverage in both directions
  (`tests/test_authz_coverage.py`).
- New tenant-scoped tables ship their RLS policy **in the same migration**, join
  `TENANT_MODELS`, and are automatically in scope for the tenancy-parity suite.
- Errors are `{"detail", "code"}` + `X-Request-ID`; services raise `AppError`,
  never `HTTPException`.
- No float in money paths. No unaudited financial mutation. No skipped tests.

Full rules: [`docs/architecture/engineering-rules.md`](./docs/architecture/engineering-rules.md).
Deployment (TLS, Cloudflare, Hostinger): [`docs/DEPLOYMENT.md`](./docs/DEPLOYMENT.md),
[`docs/DEPLOY-TLS.md`](./docs/DEPLOY-TLS.md). Milestone gate: [`docs/M0-exit-gate.md`](./docs/M0-exit-gate.md).

## License

MIT.
