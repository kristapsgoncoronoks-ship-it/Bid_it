# InvoiceIQ — Invoice Data Analytics Platform

Turn raw invoices into structured, queryable **spend analytics**. Upload invoices
(CSV/JSON today; PDF/OCR on the roadmap) → they're parsed into line items → a
dashboard answers *where the money goes, to whom, on what, and when.*

Built as a minimal but scalable production MVP: **FastAPI + PostgreSQL + React**.

> Full design write-up (architecture, schema, endpoints, UI): **[ARCHITECTURE.md](./ARCHITECTURE.md)**

## Features

- 🔐 **Multi-tenant auth** — register an org, JWT login; every row is org-scoped.
- 📥 **Ingestion** — upload structured e-invoice **XML (UBL 2.1 / UN-CEFACT CII)**, PDF, CSV, or JSON → a parsed *draft* a human confirms. Deterministic-first: e-invoice XML (incl. **Factur-X/ZUGFeRD** embedded in hybrid PDFs) is read exactly; other PDFs use the text layer, falling back to **Tesseract OCR** for scans.
- 🧾 **Invoices** — full CRUD with line items, filters, search, pagination.
- 📊 **Analytics** — KPIs, spend-over-time, top vendors, category & status breakdowns — all aggregated in the database.
- 🏷️ **Supplier benchmark** — each supplier scored **independently** (spend, invoices, effective tax, paid ratio) *and* **combined** (cross-supplier effective-unit-price comparison per category, cheapest supplier, €-savings opportunity).
- 💱 **FX vs ECB** — foreign-currency invoices converted to EUR at the **ECB reference rate** (cached in-DB, bundled fallback, live-refresh endpoint), with the EUR **markup** surfaced where a supplier billed at its own rate.
- ✅ **Data validation (opt-in)** — turn on **AI validation** (automated checks: totals, tax, per-line math, duplicates, dates, currency, FX-vs-ECB) and/or **human validation** (a review queue where a person approves/rejects). Both **off by default**, toggled by the user.
- 🧩 **Modular** — capabilities are activatable modules (core ones always on). Activating **Invoice issuing** prompts for your company registration details.
- 🧾 **EU invoice issuing** — issue **EN 16931 / Directive 2006/112/EC (Art. 226)**-compliant invoices: sequential numbering, per-rate VAT breakdown, reverse-charge / intra-EU / exempt schemes, and a **polished PDF with embedded Factur-X XML** (a hybrid e-invoice our own reader parses back).
- 🧱 **Scalable foundations** — async SQLAlchemy, stateless API, Alembic migrations, Docker, CI.

## Quick start (Docker — one command)

```bash
docker compose up --build
# web:  http://localhost:8080
# api:  http://localhost:8000/docs
```

Then seed demo data (in another shell):

```bash
docker compose exec backend python -m app.seed
# login: demo@invoiceiq.app / demo1234
```

## Quick start (local, no Docker)

Backend defaults to SQLite, so there's zero infra to run:

```bash
# 1) backend
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m app.seed                     # demo tenant + invoices
uvicorn app.main:app --reload          # http://localhost:8000/docs

# 2) frontend (new shell)
cd frontend
npm install
npm run dev                            # http://localhost:5173
```

Log in with **demo@invoiceiq.app / demo1234**, or register a fresh workspace.

## Tests

```bash
cd backend && . .venv/bin/activate && python -m pytest -q   # 12 tests
cd frontend && npm run build                                # typecheck + build
```

## Project layout

```
backend/    FastAPI app (app/), Alembic migrations, pytest suite
frontend/   React + Vite + TS SPA (TanStack Query, Recharts, Tailwind)
docker-compose.yml   postgres + backend + frontend
```

## Configuration

Backend reads env vars (see `backend/.env.example`): `DATABASE_URL`,
`SECRET_KEY` (**set in production**), `ACCESS_TOKEN_EXPIRE_MINUTES`,
`CORS_ORIGINS`. Frontend reads `VITE_API_BASE_URL` (empty = same origin).

## API

Interactive OpenAPI docs at `/docs`. Endpoint reference in
[ARCHITECTURE.md § 5](./ARCHITECTURE.md#5-api-endpoints).

## Roadmap (named seams, not yet built)

Object storage for original files, a background job queue for ingestion (OCR is
synchronous today), refresh-token rotation, richer RBAC, per-tenant data
isolation modes, and observability. Each has an explicit place to slot in — see
ARCHITECTURE.md. (PDF text-layer + OCR ingestion is now built.)

## License

MIT.
