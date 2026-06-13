# Fleet Fuel & VAT Refund System — project guide

Flask web app for fuel-invoice processing, EU VAT refunds (Dir. 2008/9/EC), and
competitor price-competitiveness intelligence, for five Baltic transport entities.

## Run it
- One-click (no terminal): double-click `start.bat` / `start.command` / `start.sh`
  → installs deps, opens browser, first-run setup wizard creates the admin account.
- Dev:        `python app.py`           (built-in server, HTTPS if cert present)
- Production: `python serve.py`         (waitress; Windows + Linux)
- Tests:      `python -m pytest tests/ -q` (315+ tests) + `python consolidate.py` smoke

## Architecture — six blocks (see README.md for the diagram)
1. Intake     `ingest.py` (xlsx/csv/xml/api), `extract.py` (PDF/ZIP→draft), `waiting_room.py` (durable queue + worker)
2. Master data `customers.db`, `suppliers.db`, `fuel_history.db` (+ `benchmark.db`, `vat_claims.db`)
               via `customer_master.py`, `supplier_master.py`, `vat_refund.py`
3. Engine     `consolidate.py`→`validate.py`→`build_master.py`→`history.py`, orchestrated by `engine_close.py`
4. Compliance `vat_refund.py` (claims, locks), `invoice_control.py` (receipt/triage)
5. Presentation `app.py` (Flask, ~25 pages + JSON API + Excel), `pricing_intelligence.py`
6. Platform   `auth.py`, `audit.py`, `backup.py`, `tls.py`, `document_vault.py`, `db.py`, `dataproduct.py`,
               `db_migrate.py`, `applog.py`, `data_lake.py`, `doc_storage.py`, `notify.py`, `process_lock.py`

## Platform capabilities — seven delegated works (the product lens)
The six blocks are the *technical* decomposition; read the product as an **accounting
platform of seven delegated works**, each owned by a module (and mostly its own DB):
1. **Data processing** — `ingest.py`/`extract.py`/`waiting_room.py` → `consolidate→validate→build_master→history` (raw files → validated, reconciled transactions).
2. **Invoice analytics & report export** — `pricing_intelligence.py`, `anomaly.py`, `contract_audit.py`, `reports.py` + the Excel exports.
3. **Digital document storage** — `document_vault.py` (local/SharePoint/FTPS), `data_lake.py`, `doc_storage.py`; SHA-256 dedup + `verify_documents`.
4. **Light CRM (API-plugin scalable)** — `customer_master.py` (entities, activation, checklist rules, templates, fees, expiry); extensibility seam = the `extract.py` parser registry / `portal_scraper.py` adapters / `/api/*`.
5. **VAT processing** — `vat_refund.py` claim lifecycle (1A→5), `vat_config.py`, claim workbook.
6. **VAT control** — `invoice_control.py` (receipt control / reconciliation) + the submission gates (checklist, doc-presence, locks, period-end).
7. **Invoicing for work** — the service-fee engine in `vat_refund.py` + `reports.fee_report_workbook` (Recovery page).
Platform floor under all seven: `auth`/`audit`/`backup`/`db`/`db_migrate`/`applog`/`tls`/`process_lock`. See `docs/PLATFORM.md`.

## Key conventions (follow these)
- Every module is location-independent: `WORKDIR = os.path.dirname(os.path.abspath(__file__))`.
- All DB access goes through each module's `connect()`; `db.py` abstracts SQLite/Postgres.
- Data-processing boundary: the ENGINE owns and WRITES the product DBs (`fuel_history.db` =
  validated `transactions`/master, `suppliers.db` = supplier master); the app reads them
  READ-ONLY via `dataproduct.connect()` — app code holds NO writable handle to the product
  DBs (a stray write raises `OperationalError`). The monthly close runs as an INDEPENDENT
  engine entrypoint `engine_close.py` (consolidate→build_master→history→run_control→backup,
  `process_lock`-guarded, single audit trail, period-stamped pickle, restartable); the close
  stage modules import side-effect-free (`history.load`/`build_master.build` are functions).
  Statement registration is ENQUEUED to the engine worker (`waiting_room` kind=`register`,
  actor propagated), not written in-request. Benchmark tables (`my_prices`/`wholesale_prices`)
  live in `benchmark.db` (app/portal-owned). See `docs/PLATFORM.md`.
- Schema migrations go through `db_migrate.apply(con, "<module>", [DDL, ...])` — a
  versioned migration table (`_ffs_migrations`) so each ALTER runs once per DB.
  APPEND new statements at the END of a module's list (positions are stable).
  Unexpected failures are logged via `applog.get(name)` (logs/app.log).
- Every data change is audit-logged with `changed_by` (see `audit.py`); web requests
  set the actor via `audit.set_actor`/`reset_actor` in app.py's before/after hooks.
- Errors self-control to the Admin panel: handled failures go BOTH to `applog`
  (logs/app.log, dev/ops) AND to the admin-facing error log via `auth.log_error(...)`
  (`error_log` in `security.db`; `recent_errors`/`clear_errors`). In app.py routes use
  `_log_exc(context, e)` in except-blocks; a global `@app.errorhandler` captures any
  unhandled exception (real HTTP 4xx/redirects pass through, never logged as errors).
  Never `except: pass` — log it.
- Backups & data integrity: `backup.snapshot/verify/restore/harden` writes
  `backups/ffs_*.zip` with a SHA-256 `MANIFEST` over the 3 data DBs + `security.db`,
  the `documents/` store and audit CSVs. `vat_refund.verify_documents()` re-hashes the
  LIVE PDF/ZIP store against `invoice_documents.sha256` to catch in-place corruption.
  Admin panel exposes `run_backup`/`verify_backup`/`verify_docs`; an opt-in scheduler
  (`backup_interval_hours` setting, leader-elected across worker processes) auto-snaps.
  Any integrity failure is written to the error log AND shown as a red banner.
- Prices everywhere are NET EUR/L, final (VAT excluded, rebates applied). State this
  basis on any new report surface.
- Extraction is deterministic-first (`extract.py`): structured e-invoices (UBL/CII XML)
  and **Factur-X/ZUGFeRD** embedded-XML inside hybrid PDFs parse with NO AI at high
  confidence (`parse_einvoice`); the embedded-XML probe runs BEFORE `pdf_text`/the
  `PARSERS` registry/AI and the ORIGINAL hybrid PDF is what gets vaulted. Only an
  unstructured PDF with no registered `parse_<x>()` falls to the AI backend, and only
  when one is configured — `parser`/`none` keep every byte on the server. AI never
  extracts a figure a structured/parser path can; it belongs to post-extraction
  validation/analytics, not capture (the **advisory AI review assistant** `ai_review.py`
  is default-OFF, sends DERIVED DATA ONLY — never the PDF/IBAN/secret — and never mutates
  or gates a figure; see `docs/AI_REVIEW.md`).
- Money is quantized via `money.py` (Decimal, ROUND_HALF_UP) — use `money.f2/fsum`
  when rounding/summing amounts and `money.q2` for EUR-threshold decisions; don't
  use bare `round()` on currency. Storage columns stay SQLite REAL.
- HTML output is escaped with `markupsafe.escape` (aliased `esc`) — never f-string
  raw DB values into a page.
- Roles: `admin` (full incl. /admin, /setup, server setup) and `processor`
  (day-to-day; capabilities are admin-configurable, never server/user admin).
  Enforced centrally in `_guard()` via `auth.has_perm` / `PERM_BY_ENDPOINT`, plus
  `ADMIN_ONLY` (the whole VAT-refund module incl. /customers CRM is admin-only)
  and `MODULES` on/off switches (Admin panel; `module_<key>` app_settings).
- VAT claim statuses are workflow CODES (1A..5, `vat_refund.STATUS_LABELS`): 1A-1E are
  SYSTEM-derived from the adjustable checklist (`customer_master.checklist_rules` +
  claim-level checks + a HARD period-end gate); manual codes map to the lock/fee
  engine via `ENGINE_OF`. 3B/3C/3D keep invoice locks; only `withdraw_claim` releases.
- A VAT claim is built from REGISTERED invoices: `vat_refund.invoice_lines` emits ONE
  row per (invoice, product code) — never an `ALL:` aggregate; an unresolved transaction
  is tagged `UNMATCHED`. `vat_refund._synthetic(ref, vat_id)` flags INPUT/ALL:/UNMATCHED,
  and the lock gate, readiness/checklist gates and `build_workbook` all REFUSE a synthetic
  line — a pack with any such line cannot be filed. Goods codes (`vat_config.GOODS_CODE`)
  follow 2008/9/EC Art. 9 / Reg. 1174/2009 & 79/2012 Annex III (1 fuel, 4 road tolls,
  10 other — NEVER 9 = luxuries/entertainment). See `docs/VAT_REFUND_RULES.md`.
- NET/effective price = `net_eur_eff / qty`. City dimension = the `station` column.

## Do NOT commit
Secrets (.secret_key, certs), `security.db` (password hashes), generated Excel,
runtime dirs (backups/, inbox/). See `.gitignore`.

## Testing
`python -m pytest tests/ -q` — the full suite (claims workflow, checklist, CRM
templates, migrations, web/security, intake, FX, …). `python consolidate.py` must
PASS all suppliers. After test runs, restore demo-DB churn before committing:
`git restore customers.db fuel_history.db suppliers.db` and delete runtime DBs.

## Common tasks
- Add a supplier (data): `supplier_master.py` + set `invoice_cadence`; register a statement.
- Add a supplier PDF parser: add a `parse_<x>()` to the PARSER REGISTRY in `extract.py`.
- Monthly close: edit `month_config.py`, then run the orchestrator `python engine_close.py
  [period]` (consolidate→build_master→history→run_control→backup, one audit trail, restartable).
  The individual CLIs still run standalone for debugging.

## Known next steps (backlog)
- Money sweep (full precision / `money.f2`) for the stored-master and analytics paths
  still on bare `round()`: `build_master.py`, `queries.py`/`reports.py` overpay+total
  (consolidate the duplicated overpay loop into one canonical impl), and the
  `_num` fallback in `extract.py`'s e-invoice branch.
- Test coverage for `invoice_control.py`, `ingest.py`, `build_master.py`, `history.py`.
- Migrate the remaining ad-hoc `except: pass` blocks to `applog`/`_log_exc` logging.
- PDF generation for .docx templates (text templates already export PDF).
- Notifications: `notify.py` digest mailer (worklist + expiring docs + stuck queue jobs) on
  a leader-elected scheduler is DONE; remaining = per-event alerts + an SMTP relay config UI.
- Reliability hardening (`docs/RELIABILITY.md`): dead-letter/DLQ growth alerting, an
  oldest-pending-job age SLO metric, `process_lock` fencing token + monotonic-clock deadline,
  a per-job extract deadline, and the register-failure vaulted-doc reconcile.
- Off-machine backup sync (OneDrive/SharePoint) so `backups/` survives disk loss —
  currently an OS/cron concern, documented in the Admin "Backups" card.
- Horizontal scale (see `docs/SCALING.md`). Done & testable: node roles (`FFS_ROLE`),
  shared sessions (`FFS_SECRET_KEY`), pluggable storage, lease queue + leader scheduler,
  and the `db.qmark_to_pyformat` paramstyle shim. Remaining for a validated Postgres
  cutover (needs a LIVE Postgres): exercise `_PgShim` on real psycopg; port dialect-isms
  (`datetime('now')`→`now()`, `INSERT OR IGNORE`→`ON CONFLICT`, `json_object` audit
  triggers → a PG trigger fn); migrate the `intake` queue + `process_lock` leases to the
  shared DB; Postgres-native backups (`pg_dump`).
