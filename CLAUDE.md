# Fleet Fuel & VAT Refund System — project guide

Flask web app for fuel-invoice processing, EU VAT refunds (Dir. 2008/9/EC), and
competitor price-competitiveness intelligence, for five Baltic transport entities.

## Run it
- One-click (no terminal): double-click `start.bat` / `start.command` / `start.sh`
  → installs deps, opens browser, first-run setup wizard creates the admin account.
- Dev:        `python app.py`           (built-in server, HTTPS if cert present)
- Production: `python serve.py`         (waitress; Windows + Linux)
- Tests:      see "Testing" below (no pytest yet — smoke tests are inline)

## Architecture — six blocks (see README.md for the diagram)
1. Intake     `ingest.py` (xlsx/csv/xml/api), `extract.py` (PDF/ZIP→draft)
2. Master data 3 SQLite DBs: `customers.db`, `suppliers.db`, `fuel_history.db`
               via `customer_db.py`, `supplier_db.py`, `vat_refund.py`
3. Engine     `consolidate.py`→`validate.py`→`build_master.py`→`history.py`
4. Compliance `vat_refund.py` (claims, locks), `invoice_control.py` (receipt/triage)
5. Presentation `app.py` (Flask, ~18 pages + JSON API + Excel), `pricing_intel.py`
6. Platform   `auth.py`, `audit.py`, `backup.py`, `tls.py`, `doc_storage.py`, `db.py`

## Key conventions (follow these)
- Every module is location-independent: `WORKDIR = os.path.dirname(os.path.abspath(__file__))`.
- All DB access goes through each module's `connect()`; `db.py` abstracts SQLite/Postgres.
- Schema migrations are `try: ALTER TABLE ... except sqlite3.OperationalError: pass`
  (column-exists guard) — keep `import sqlite3` at module top when using this.
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
  Enforced centrally in `_guard()` via `auth.has_perm` / `PERM_BY_ENDPOINT`.
- NET/effective price = `net_eur_eff / qty`. City dimension = the `station` column.

## Do NOT commit
Secrets (.secret_key, certs), `security.db` (password hashes), generated Excel,
runtime dirs (backups/, inbox/). See `.gitignore`.

## Testing
No pytest suite yet — the convention has been inline smoke tests:
`python consolidate.py` must PASS all suppliers; the app must serve all pages 200
when logged in. A good next task: extract these into `tests/` with pytest.

## Common tasks
- Add a supplier (data): `supplier_db.py` + set `invoice_cadence`; register a statement.
- Add a supplier PDF parser: add a `parse_<x>()` to the PARSER REGISTRY in `extract.py`.
- Monthly close: edit `month_config.py` → `consolidate.py` → `build_master.py` →
  `history.py` → `invoice_control.py <period>` → `backup.py`.

## Known next steps (backlog)
- Wholesale price feed for true-margin view (`pricing_intel.wholesale_prices`).
- Extract inline smoke tests into a pytest suite.
- Logging layer to replace remaining `except: pass` migration guards.
- Migration-version table so ALTERs run once instead of every connect().
