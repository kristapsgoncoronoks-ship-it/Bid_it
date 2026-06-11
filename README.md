# Fleet Fuel & VAT Refund System

Self-contained system for multi-entity fleet fuel management: invoice extraction,
supplier comparison, EU VAT refunds (Directive 2008/9/EC), receipt controls and a
web UI. Runs anywhere with Python 3.12+ (`pip install flask openpyxl --break-system-packages`;
`requests` only for live API pulls / SharePoint). Everything lives in this one folder —
code, databases, documents, reports — so the folder IS the backup unit.

## Architecture

    SOURCES (xlsx / csv / xml / api)
        └─ ingest.py ──► consolidate.py (validates vs invoice totals) ──► canonical lines
                                  │
        ┌─────────────────────────┼──────────────────────────────┐
        ▼                         ▼                              ▼
    build_master.py          history.py                    web UI app.py
    (monthly master xlsx)    (fuel_history.db + trend)     (12 pages + JSON API)

    MASTER DATA (separated)            CLAIMS & CONTROLS (fuel_history.db)
      customers.db  - our entities       vat_applications  - claim lifecycle
      suppliers.db  - suppliers,         vat_claimed_invoices - one-invoice-one-
                      VAT regs, banks,                        submission locks
                      products,          invoice_documents - vault index (SHA-256)
                      invoices,          invoice_receipt_control - cadence checks
                      statements
    All tables audit-logged by SQLite triggers (audit.py) - full old/new history.

## File map

**Monthly pipeline**
- `month_config.py` — the ONLY file edited monthly: period, files, FX
- `ingest.py` — source adapters: xlsx / csv / xml / api (self-test: `python3 ingest.py`)
- `supplier_specs.py` — trainable supplier registry (row maps + validation targets)
- `consolidate.py` — maps to canonical schema, refuses to build on validation FAIL
- `build_master.py` — Fleet_Fuel_Master_<period>.xlsx (benchmark, comparison, stations, VAT)
- `history.py` — loads period into fuel_history.db (idempotent) + trend report

**Master data** (separate databases)
- `customer_db.py` / `customers.db` — entities: reg number, VAT, address, payout IBAN, portals
- `supplier_db.py` / `suppliers.db` — suppliers: legal data, per-country VAT regs, banks,
  product catalogs, invoice registry, statements, cadence
- `audit.py` — trigger-based change log in every DB; `history()`, `diff()`, `as_of()`

**VAT refunds & controls**
- `vat_config.py` — regulatory constants: goods codes (Reg. 1174/2009), 400/50 EUR minimums,
  deadline rule, compliance notes
- `vat_refund.py` — claims per ENTITY x COUNTRY x PERIOD (Q1–Q4 + YEAR), thresholds,
  claim packs with APPLICANT block, duplicate-submission locks, document vault
- `pricing_intel.py` — competitor NET-price tracking & margin analysis; combinable
  daily/weekly/monthly from one daily grain; three baselines (MY Price, supplier-pack,
  wholesale index); Excel grid export for pricing models
- `validate.py` — line-level cross-checks (VAT rate, signs, ranges, batch tie-out) +
  regression store; blocks commit on errors
- `anomaly.py` — relative anomaly scan (station price, MoM jumps, volume spikes, off-period)
- `db.py` — database abstraction; SQLite default, one env var switches to PostgreSQL
- `watch_inbox.py` — optional folder watcher: auto-extract dropped PDF/ZIP into review queue
- `extract.py` — PDF/ZIP batch extraction: deterministic parser (offline) or AI
  backend (Claude/OpenAI/Azure, pluggable via EXTRACT_BACKEND); produces a DRAFT only
- `invoice_control.py` — receipt control (cadence x activity) + statement reconciliation
  with VAT triage (PROCESS / DISCARD / DISCARD-DOMESTIC)
- `doc_storage.py` — vault backends: local folder (default) or SharePoint via MS Graph
  (setup steps in file header; set `DOC_BACKEND=sharepoint` + `SP_*` env vars)

**Install & run**
- `install.sh` / `install.bat` — one-click guided setup (runs `setup_wizard.py`)
- `start.py` + `start.bat`/`start.sh`/`start.command` — one-click launch: installs
  deps, starts the server, opens the browser to the first-run setup page (no terminal)
- `setup_wizard.py` — the wizard itself; re-runnable; `--yes` mode for IT automation

**UI & util**
- `app.py` — `python3 app.py` → http://localhost:8050. Pages: Dashboard, Compare,
  Head-to-head, Entities & VAT, Stations, Invoice control, VAT refunds, Documents,
  Suppliers, Customers, Data manager, History. Exports + `/api/*` JSON.
  Team deployment: `gunicorn -w 2 app:app` behind nginx (auth at proxy).
- `cleanup.py` — safely stops a running app.py (never `pkill -f app.py`: it matches itself)

**Data & reports in this folder**
- `customers.db`, `suppliers.db`, `fuel_history.db`, `documents/` — system of record
- `Fleet_Fuel_Master_2026-05.xlsx`, `Fleet_Fuel_History_Report.xlsx`,
  `VAT_Refund_Claims_2026.xlsx` — current deliverables
- supplier transaction workbooks (`*_transactions.xlsx`) — pipeline inputs for 2026-05
- `demo_supplier_invoice.xml`, `demo_api_response.json` — ingest fixtures

## Monthly close (runbook)

1. Drop supplier files (or pull via API) and edit `month_config.py`
2. `python3 consolidate.py` — must PASS every supplier vs its invoice totals
3. `python3 build_master.py` — master workbook
4. `python3 history.py` — archive + trend report
5. `python3 invoice_control.py <period>` — receipt control: chase MISSING, attach docs
6. Register supplier statements (UI → Invoice control) — triage PROCESS / DISCARD;
   domestic invoices auto-discard (home-country VAT → regular VAT return)
7. Review payments calendar (master workbook / Entities page)

## Quarterly VAT refunds

1. After quarter end: `python3 vat_refund.py <year>` → VAT_Refund_Claims_<year>.xlsx
2. Per READY stream: fill yellow INPUTs (invoice refs, VAT IDs, applicant data),
   confirm every invoice has its original in the vault (Documents page)
3. File via the entity's home portal (e-MTA / EDS / Mano VMI); set status `submitted`
   in the UI — this LOCKS the invoices (one invoice = one submission, ever);
   `rejected`/`withdrawn` releases locks
4. Deadline: 30 September of the following year. Below 400 EUR/quarter → annual claim.

## Built-in controls

- Extraction validation: every supplier must reconcile to its invoice before anything builds
- Receipt control: expected = cadence (14/30 days, per suppliers.db) x activity; orphan check
- Statement reconciliation: supplier's issue list vs registry vs vault; VAT triage with
  automatic DOMESTIC discard (entity-relative)
- Duplicate-submission lock: DB UNIQUE constraint; quarterly-vs-annual overlap blocked
- Document gate: no submission without original PDF/scan (SHA-256, wrong-file detection)
- Audit: every insert/update/delete in every DB logged with old/new snapshots;
  date-range queries + as-of reconstruction

## Open items / missing data (yellow INPUT fields)

- Customer master: registration codes (5), VAT numbers (OMUSS, Motiejausko),
  legal addresses (3), refund payout IBANs (all 6 entities)
- Supplier master: Q8 per-country VAT IDs + 5 country invoice originals (FR/ES/DK/PL/AT),
  BP NIP, TFC BE VAT number, Port One details
- Per-issuer confirmation whether the card scheme is refundable under 2008/9/EC
  (ECJ Vega International) or runs its own refund service
