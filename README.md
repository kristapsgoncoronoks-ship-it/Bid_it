# InvoiceIQ — Invoice Data Analytics Platform

Turn raw invoices into structured, queryable **spend analytics**. Upload invoices
(CSV/JSON today; PDF/OCR on the roadmap) → they're parsed into line items → a
dashboard answers *where the money goes, to whom, on what, and when.*

Built as a minimal but scalable production MVP: **FastAPI + PostgreSQL + React**.

> Full design write-up (architecture, schema, endpoints, UI): **[ARCHITECTURE.md](./ARCHITECTURE.md)**

## Features

- 🔐 **Multi-tenant SaaS** — register an org, JWT login; every row is org-scoped. Team members via **token invitations**, **subscription plans + seat limits**, plan-gated modules, tenant suspension, and a **platform operator** view across tenants.
- 👤 **Four user groups + a system matrix** — `User-free` (non-paying, limited), `User` (paying), `Admin` (admin-panel access), `Sysadmin` (all privileges incl. user-rights management). A sysadmin-editable **Access & limits matrix** sets each group's monthly usage limits (0 = unlimited); free/paid tiers are capped and enforced at invoice creation, admins/sysadmins are unlimited.
- 📥 **Ingestion** — upload structured e-invoice **XML (UBL 2.1 / UN-CEFACT CII)**, PDF, CSV, or JSON → a parsed *draft* a human confirms. Deterministic-first: e-invoice XML (incl. **Factur-X/ZUGFeRD** embedded in hybrid PDFs) is read exactly; other PDFs use the text layer, falling back to **Tesseract OCR** for scans.
- 📧 **Email invoice intake** — each workspace gets a dedicated inbound address (`<token>@…`); forward or auto-route supplier invoices there and an email provider's inbound-parse webhook drops the attachments into a **review inbox**, auto-parsed through the same engine. Confirm each draft into an invoice (the *identical* persistence path as an upload), or discard. A rotatable address, tenant-scoped, module-gated.
- 🏠 **Monthly budgeting (household / personal)** — treat received invoices as a personal or household budget: set a recurring **monthly limit per category** and track it against the invoices you actually received. Actuals are VAT-inclusive and ECB-converted to EUR (same fact grain as the analytics); the page shows budget-vs-actual per category with progress bars, over/under, an overall total, and a 6-month trend. Module-gated (included on every plan).
- 🧾 **Invoices** — full CRUD with line items, filters, search, pagination.
- 📊 **Analytics** — KPIs, spend-over-time, top vendors, category & status breakdowns — all aggregated in the database.
- 🔎 **Explore (self-service BI)** — a Power BI / Tableau-style pivot builder: pick any **measure** (net/tax/gross/quantity/counts) × up to two **dimensions** (vendor, category, status, currency, country, month/quarter/year) × filters, visualize (bar/line/pie/stacked/table), and export CSV. Aggregated in the DB over an indexed line-item fact grain.
- 🏷️ **Supplier benchmark** — each supplier scored **independently** (spend, invoices, effective tax, paid ratio) *and* **combined** (cross-supplier effective-unit-price comparison per category, cheapest supplier, €-savings opportunity).
- 💱 **FX vs ECB — all European currencies** — every European currency runs against the euro: official **ECB reference rates** for the ~12 ECB-published ones (EU/EEA + UK, cached in-DB, live-refresh endpoint) plus **indicative** rates for the wider-Europe currencies ECB doesn't publish (RSD, BAM, MKD, ALL, MDL, UAH, GEL, AMD, AZN, BYN, RUB, GIP). Coverage is guaranteed on every boot; a converter + `/fx/currencies` list them, and the EUR **markup** is surfaced where a supplier billed at its own rate.
- ✅ **Data validation (opt-in)** — turn on **AI validation** (automated checks: totals, tax, per-line math, duplicates, dates, currency, FX-vs-ECB) and/or **human validation** (a review queue where a person approves/rejects). Both **off by default**, toggled by the user.
- 🧩 **Modular** — capabilities are activatable modules (core ones always on). Activating **Invoice issuing** prompts for your company registration details.
- 🧾 **EU invoice issuing** — issue **EN 16931 / Directive 2006/112/EC (Art. 226)**-compliant invoices: sequential numbering, per-rate VAT breakdown, reverse-charge / intra-EU / exempt schemes, and a **polished PDF with embedded Factur-X XML** (a hybrid e-invoice our own reader parses back).
- 🧳 **Employee expenses (SAP Concur-style)** — **import a bank statement** (PDF via OCR, or CSV) into an **available-expenses inbox** (a separate reader, built on the same engine, that tells the transaction amount from the running balance); pick transactions to build a report, **attach a receipt document** per entry, add **per-entry comments** and a **report comment thread** (employee ↔ approver). Per-employee ownership within the tenant; managers approve/reject/reimburse. Reclaimable VAT + FX-to-EUR (ECB) + PDF export.
- 🛡️ **Upload security** — every uploaded/emailed file passes a central gate (`services/filesec.py`) before any parsing or OCR: size cap, **magic-byte type validation** (a `.pdf` that is really HTML, an EXE/ELF/Mach-O, a shell script or a zip is rejected), and a **malware scan** (EICAR always, optional **ClamAV** — fail-closed when configured). Malicious email attachments are quarantined (metadata kept, bytes dropped, never parsed/stored); XML is hardened with `defusedxml` (XXE / billion-laughs); files are served inert (`attachment` + `nosniff`).
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

## Database migrations

Dev/test create tables directly (zero-setup). **Production owns schema evolution
through Alembic** — `create_all` is skipped when `ENVIRONMENT=production`, and the
prod compose runs `alembic upgrade head` before booting the API. To evolve the
schema: change the models, then `make migration m="add x"` (autogenerate) and
review the file; deploys apply it automatically. Apply manually with `make migrate`.

## TLS / SSL + Cloudflare

Production runs behind Cloudflare with the nginx container as the TLS origin —
Cloudflare Origin Certificate (SSL mode *Full (Strict)*), real-visitor-IP
restoration from `CF-Connecting-IP`, HSTS + security headers, and the backend
made proxy-aware. One command with the prod override
(`docker compose -f docker-compose.yml -f docker-compose.prod.yml up`). Full
walkthrough: **[docs/DEPLOY-TLS.md](./docs/DEPLOY-TLS.md)**.

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
