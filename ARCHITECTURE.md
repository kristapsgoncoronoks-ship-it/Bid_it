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
| **Ingestion** | Synchronous parse for PDF/CSV/JSON (PDF: text layer → Tesseract OCR fallback) | The parser is isolated behind a service interface → move OCR to a queue (Celery/Arq + Redis) + object storage (S3) for originals without touching the API. |
| **Frontend** | SPA + cached queries (TanStack Query) | Static assets on a CDN; server does data only. |
| **Migrations** | Alembic | Zero-downtime schema evolution. |

### Ingestion (deterministic-first)
`services/parser.py` dispatches by file type / content, cheapest-and-most-exact first:

1. **Structured e-invoice XML** (`services/einvoice.py`) — **UBL 2.1** and
   **UN-CEFACT CII** (EN-16931). Every figure is read from a typed field — no
   guessing. Hardened with `defusedxml` against XXE/entity attacks; navigation is
   by local element name so it's robust across schema versions.
2. **Factur-X / ZUGFeRD hybrid PDFs** — before any OCR, the PDF is probed for an
   embedded e-invoice XML attachment (`factur-x.xml` etc.); if present it's read
   via path 1 — exact, no OCR.
3. **PDF text layer** (`services/pdf_ocr.py`, pdfplumber) — exact when the PDF
   carries real text.
4. **Tesseract OCR** — only for scanned/image-only PDFs: pypdfium2 rasterises,
   then `image_to_data` word boxes are re-clustered into rows so tables survive.
   The row parser is **transaction-table-aware**: it reads *every* line of a
   multi-row statement (fuel/toll card statements, etc.), masks dates/times so a
   transaction date isn't mistaken for an amount, keeps the date on the line
   description, parses money locale-aware (1,234.56 / 1.234,56), and only skips
   genuine total/summary rows (a station named "Total …" is kept). A heuristic
   then derives header fields + an inferred VAT rate.

All paths produce the same confirmable draft. OCR runs synchronously today; the
service boundary is the seam to move it onto a queue.

### Deliberately deferred (documented, not built)
Object storage for original files, background workers (async OCR), rate
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
| GET  | `/analytics/supplier-benchmark` | Per-supplier scorecards (independent) |
| GET  | `/analytics/combined-benchmark` | Cross-supplier price benchmark per category + savings |
| GET  | `/fx/rates` | Cached ECB reference rates (units per EUR) |
| GET  | `/fx/convert` | Convert an amount between currencies at ECB |
| GET  | `/fx/ecb-comparison` | Foreign invoices valued at ECB + stated-rate markup |
| POST | `/fx/refresh` | Pull live ECB rates into the cache (owner only) |
| GET/PUT | `/settings/validation` | Read / toggle AI + human validation (PUT owner only) |
| POST | `/invoices/{id}/validate` | Human review: approve / reject |
| GET | `/modules` · PUT `/modules/{key}` | Module registry + activation (PUT owner only) |
| GET/PUT | `/issuer` · POST `/issuer/logo` | Company registration details (the seller) |
| POST/GET | `/issued` · GET `/issued/{id}` | Create / list / view issued invoices |
| GET | `/issued/{id}/pdf` · `/issued/{id}/xml` | Hybrid Factur-X PDF · standalone EN-16931 XML |

Interactive contract: `http://localhost:8000/docs` (OpenAPI).

## 6. UI architecture

Single-page React app. `AuthContext` holds the JWT (in memory; refresh on reload
via `/auth/me`). `ProtectedRoute` guards the app shell. Data fetching is
**TanStack Query** so every screen is cached, deduped, and revalidated.

- **Dashboard** — KPI row + spend-over-time line chart + top-vendors bar +
  category pie + status breakdown.
- **Benchmark** — two tabs: *Combined* (savings headline + per-category
  supplier price tables, cheapest flagged) and *Independent* (per-supplier
  scorecards).
- **FX** — ECB converter, foreign-invoice-vs-ECB comparison table (markup
  flagged), and the ECB reference-rate grid.
- **Settings** — toggle AI / human validation + activate modules (owner only).
- **Review** — the human-validation queue (pending + AI-flagged), approve/reject.
- **Issue** (module-gated) — company details form, new-invoice form, issued list
  with PDF/XML download.

### Modular platform & EU invoice issuing
`services/modules.py` is a registry of capabilities: `core` ones are always on;
add-ons (`issuing`) are activated per-org (`org_modules`). Nav and the issuing
routes are gated on the module. **Invoice issuing** requires a complete issuer
profile (`services/issuer.py`, Art. 226 seller identity) before use. Issued
invoices (`issued_invoices`, separate from received/analysed invoices) get
sequential numbers per issuer, a per-VAT-rate breakdown (`services/vat.py`,
incl. reverse-charge / intra-EU / exempt with the required legal note), a frozen
seller snapshot, an **EN 16931 CII XML** (`services/facturx.py`, the outbound
twin of `einvoice`), and a **polished PDF with that XML embedded**
(`services/invoice_pdf.py`, reportlab + pypdf) — a hybrid Factur-X document our
own `einvoice.extract_embedded_xml` round-trips.

### Data validation (opt-in)
Two independent options, OFF by default and turned on per-org by the user
(`services/validation.py`). **AI validation** runs an automated rule engine on
each saved invoice (money consistency, per-line math, duplicates, dates,
currency, FX-vs-ECB) and records findings — advisory, resolves to `passed` or
`flagged`. `ai_enrich()` is the seam for a real LLM (default no-op, nothing
leaves the server). **Human validation** routes the invoice to a review gate
(`pending`) until a person approves/rejects. With both on, AI findings assist the
reviewer and the invoice still waits for a human. With neither, status is `none`.

### FX & ECB rates
`services/fx.py` caches ECB euro reference rates in `ecb_rates` (units per EUR).
The request path never hits the network: rates come from the DB, seeded from a
bundled snapshot on first run and refreshed by `POST /fx/refresh` (pulls the ECB
feed when the host is reachable, fails gracefully otherwise). `rate_for` uses the
latest rate on-or-before a date. Non-EUR invoices store `total_eur` + `fx_source`
(stated rate if the invoice carries one, else ECB); the comparison surfaces the
EUR markup between a supplier's stated rate and ECB.
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
