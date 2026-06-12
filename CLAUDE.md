# Fleet Fuel & VAT Refund System — project guide

Flask web app for fuel-invoice processing, EU VAT refunds (Dir. 2008/9/EC), and
competitor price-competitiveness intelligence, for five Baltic transport entities.

## Run it
- One-click (no terminal): double-click `start.bat` / `start.command` / `start.sh`
  → installs deps, opens browser, first-run setup wizard creates the admin account.
- Dev:        `python app.py`           (built-in server, HTTPS if cert present)
- Production: `python serve.py`         (waitress; Windows + Linux)
- Tests:      `python -m pytest tests/ -q` (190+ tests) + `python consolidate.py` smoke

## Architecture — six blocks (see README.md for the diagram)
1. Intake     `ingest.py` (xlsx/csv/xml/api), `extract.py` (PDF/ZIP→draft)
2. Master data 3 SQLite DBs: `customers.db`, `suppliers.db`, `fuel_history.db`
               via `customer_master.py`, `supplier_master.py`, `vat_refund.py`
3. Engine     `consolidate.py`→`validate.py`→`build_master.py`→`history.py`
4. Compliance `vat_refund.py` (claims, locks), `invoice_control.py` (receipt/triage)
5. Presentation `app.py` (Flask, ~18 pages + JSON API + Excel), `pricing_intelligence.py`
6. Platform   `auth.py`, `audit.py`, `backup.py`, `tls.py`, `document_vault.py`, `db.py`

## Key conventions (follow these)
- Every module is location-independent: `WORKDIR = os.path.dirname(os.path.abspath(__file__))`.
- All DB access goes through each module's `connect()`; `db.py` abstracts SQLite/Postgres.
- Schema migrations go through `db_migrate.apply(con, "<module>", [DDL, ...])` — a
  versioned migration table (`_ffs_migrations`) so each ALTER runs once per DB.
  APPEND new statements at the END of a module's list (positions are stable).
  Unexpected failures are logged via `applog.get(name)` (logs/app.log).
- Every data change is audit-logged with `changed_by` (see `audit.py`); web requests
  set the actor via `audit.set_actor`/`reset_actor` in app.py's before/after hooks.
- Prices everywhere are NET EUR/L, final (VAT excluded, rebates applied). State this
  basis on any new report surface.
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
- Monthly close: edit `month_config.py` → `consolidate.py` → `build_master.py` →
  `history.py` → `invoice_control.py <period>` → `backup.py`.

## Known next steps (backlog)
- PDF generation for .docx templates (text templates already export PDF).
- Notifications (email) for worklist items: deadlines, expiring documents.
- Migrate remaining ad-hoc `except: pass` blocks to `applog` logging.
