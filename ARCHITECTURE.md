# InvoiceIQ — Invoice Data Analytics Platform

> A production-ready MVP that turns raw invoices into structured, queryable spend
> analytics. Built minimal, but on foundations that scale to millions of invoices
> and thousands of tenants.

## 1. Product in one line

Upload invoices (CSV / JSON today, PDF/OCR on the roadmap) → they are parsed into
**structured line items** → an analytics layer answers *"where is my money going,
to whom, on what, and when — and what looks wrong?"*

## 2. System architecture

```
                        ┌──────────────────────────────────────────┐
                        │              Browser (SPA)                │
                        │   React + Vite + TS + TanStack Query      │
                        │   Recharts dashboards · JWT in memory     │
                        └───────────────┬──────────────────────────┘
                                        │  HTTPS / JSON
                                        ▼
                        ┌──────────────────────────────────────────┐
                        │            API Gateway (FastAPI)          │
                        │  • JWT auth + per-tenant scoping          │
                        │  • Pydantic v2 validation                 │
                        │  • Routers: auth / vendors / invoices /   │
                        │    analytics                              │
                        └──────┬───────────────────────┬───────────┘
                               │                        │
                     ┌─────────▼────────┐     ┌─────────▼──────────┐
                     │  Service layer   │     │  Ingestion/Parser  │
                     │  analytics.py    │     │  services/parser   │
                     │  (aggregations)  │     │  CSV/JSON → draft  │
                     └─────────┬────────┘     └─────────┬──────────┘
                               │                        │
                        ┌──────▼────────────────────────▼──────┐
                        │      PostgreSQL (SQLAlchemy 2.0)      │
                        │  orgs · users · vendors · invoices ·  │
                        │  line_items   (all tenant-scoped)     │
                        └───────────────────────────────────────┘
```

### Why these choices (the "scale to millions" story)

| Concern | MVP decision | Scales because |
|---|---|---|
| **Backend** | FastAPI (async) | Non-blocking I/O; horizontal stateless replicas behind a load balancer. |
| **DB** | Postgres, async driver (`asyncpg`) | Proven to billions of rows; add read-replicas + partition `invoices`/`line_items` by `org_id`/month later. |
| **Multi-tenancy** | Row-level `org_id` on every table + enforced in a single query dependency | Shared-schema is cheapest to run; the same code moves to schema-per-tenant or RLS with no API change. |
| **Auth** | Stateless JWT | No session store; any replica validates a token. |
| **Ingestion** | Synchronous parse for CSV/JSON | The parser is already isolated behind a service interface → drop in a queue (Celery/Arq + Redis) + object storage (S3) for PDFs/OCR without touching the API. |
| **Frontend** | SPA + cached queries (TanStack Query) | Static assets on a CDN; server does data only. |
| **Migrations** | Alembic | Zero-downtime schema evolution. |

### Deliberately deferred (documented, not built)
Object storage for original files, OCR/PDF extraction, background workers, rate
limiting, refresh-token rotation, RBAC beyond owner/member, audit log, and
observability (OpenTelemetry). Each has a named seam in the code so it can be
added without a rewrite.

## 3. File structure

```
Bid_it/
├── docker-compose.yml          # postgres + backend + frontend, one command
├── Makefile                    # dev shortcuts
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI app + lifespan
│   │   ├── core/               # config, database engine, security (JWT/hash)
│   │   ├── models/             # SQLAlchemy 2.0 ORM (tenant-scoped)
│   │   ├── schemas/            # Pydantic v2 request/response contracts
│   │   ├── api/                # deps (auth/session) + routers
│   │   ├── services/           # parser (ingestion) + analytics (aggregations)
│   │   └── seed.py             # demo tenant + invoices
│   ├── alembic/                # migrations
│   └── tests/                  # pytest (auth, invoices, analytics)
└── frontend/
    └── src/
        ├── lib/                # api client, types, formatters
        ├── auth/               # auth context + guard
        ├── components/         # layout, KPI cards, charts
        └── pages/              # Login, Dashboard, Invoices, Detail, Upload
```

## 4. Database schema

`organizations (1) ──< users` and `organizations (1) ──< vendors ──< invoices ──< line_items`.
Every business row carries `org_id`; nothing is queried without it.

See `backend/app/models/` for the source of truth. Summary:

- **organizations** — tenant root: `id, name, created_at`.
- **users** — `id, org_id, email (unique), name, hashed_password, role, is_active`.
- **vendors** — `id, org_id, name, tax_id, country, category`; unique `(org_id, name)`.
- **invoices** — `id, org_id, vendor_id, invoice_number, issue_date, due_date,
  currency, status, subtotal, tax_amount, total, source_filename`.
- **line_items** — `id, invoice_id, description, category, quantity, unit_price,
  amount, tax_rate`.

Money is `Numeric(14,2)` (exact decimals, never floats). Indices on every
`org_id`, on `invoices(issue_date)` and `line_items(category)` for the analytics
group-bys.

## 5. API endpoints

Base path `/api/v1`. All non-auth routes require `Authorization: Bearer <jwt>`
and are automatically scoped to the caller's organization.

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/register` | Create org + owner user, return token |
| POST | `/auth/login` | Email+password → JWT |
| GET  | `/auth/me` | Current user + org |
| GET/POST | `/vendors` | List / create vendors |
| GET  | `/invoices` | List with filters (vendor, status, date range, search, paging) |
| POST | `/invoices` | Create invoice + line items |
| GET  | `/invoices/{id}` | Invoice detail with line items |
| PATCH| `/invoices/{id}` | Update status/fields |
| DELETE | `/invoices/{id}` | Delete |
| POST | `/invoices/upload` | Upload CSV/JSON → parsed draft invoice |
| GET  | `/analytics/summary` | KPIs (spend, tax, unpaid, counts) |
| GET  | `/analytics/spend-over-time` | Monthly spend series |
| GET  | `/analytics/top-vendors` | Spend by vendor |
| GET  | `/analytics/by-category` | Spend by line-item category |
| GET  | `/analytics/by-status` | Count/amount by invoice status |

Interactive contract: `http://localhost:8000/docs` (OpenAPI).

## 6. UI architecture

Single-page React app. `AuthContext` holds the JWT (in memory; refresh on reload
via `/auth/me`). `ProtectedRoute` guards the app shell. Data fetching is
**TanStack Query** so every screen is cached, deduped, and revalidated.

- **Dashboard** — KPI row + spend-over-time line chart + top-vendors bar +
  category pie + status breakdown.
- **Invoices** — filterable, paged table.
- **Invoice detail** — header + line-item table + status control.
- **Upload** — drag a CSV/JSON, preview the parsed draft, confirm to persist.

## 7. Running it

```bash
make up            # docker-compose: postgres + api + web
# or locally:
make backend       # uvicorn on :8000  (SQLite fallback if no Postgres)
make frontend      # vite on :5173
make seed          # demo tenant: demo@invoiceiq.app / demo1234
make test          # backend pytest suite
```
