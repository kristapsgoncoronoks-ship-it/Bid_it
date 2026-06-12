# Architecture

This document describes how the Fleet Fuel & VAT Refund System is put together: the
six building blocks, the data model, the module map, and the conventions that keep it
consistent. For setup see **[INSTALL.md](INSTALL.md)**; for day‑to‑day use see
**[USER_MANUAL.md](USER_MANUAL.md)**.

---

## Design principles

- **One folder, copy‑anywhere.** Every module is location‑independent
  (`WORKDIR = os.path.dirname(os.path.abspath(__file__))`) and opens its database as
  `{WORKDIR}/<name>.db`. The modules import each other flatly. So the whole system is a
  single directory you can copy, zip, or back up as a unit — which is why the files are
  flat in the repo root rather than nested in packages.
- **Small bricks.** Logic is split across many focused modules instead of a few large
  files, to keep memory and parsing light and the code easy to navigate.
- **Human‑in‑the‑loop.** Extraction (even AI) only ever produces a *draft*; nothing is
  written to the books until a person reviews and confirms it.
- **Everything is audited.** Inserts/updates/deletes on every database are captured by
  SQLite triggers with full old/new snapshots and the acting user.

---

## The six blocks

```
1. INTAKE          ingest.py (xlsx/csv/xml/api), extract.py (PDF/ZIP → draft),
                   intake_queue.py (durable "waiting room" + background worker)
        │
2. MASTER DATA     customers.db (customer_db.py)   suppliers.db (supplier_db.py)
                   fuel_history.db (vat_refund.py / history.py)
        │
3. ENGINE          consolidate.py → validate.py → build_master.py → history.py
        │
4. COMPLIANCE      vat_refund.py (claims, locks, fees, vault index),
                   invoice_control.py (receipt control / statement triage)
        │
5. PRESENTATION    app.py (Flask: ~25 pages + JSON API + Excel), pricing_intel.py,
                   reports.py, anomaly.py
        │
6. PLATFORM        auth.py, audit.py, backup.py, tls.py, doc_storage.py, db.py,
                   dbtune.py, proclock.py
```

---

## Data model (three databases, separated on purpose)

| Database | Owner module | Key tables |
|----------|--------------|------------|
| `customers.db` | `customer_db.py` | `customers` (reg/VAT/payout/fee), `customer_bank_accounts`, `customer_documents`, `customer_countries` (per‑country activation), `customer_fees`, `country_requirements` |
| `suppliers.db` | `supplier_db.py` | `suppliers` (+ cadence), `supplier_vat_registrations`, `supplier_bank_accounts`, `supplier_products`, `supplier_invoices`, `supplier_statements` |
| `fuel_history.db` | `vat_refund.py`, `history.py`, `invoice_control.py` | `transactions` (canonical fuel lines), `vat_applications` (claim lifecycle + fees), `vat_claimed_invoices` (one‑invoice‑one‑submission locks), `invoice_documents` (vault index, SHA‑256), `invoice_receipt_control` |
| `security.db` *(not committed)* | `auth.py` | `users` (scrypt hashes), `role_permissions`, `login_log`, `error_log`, `app_settings` |
| `intake.db` *(operational)* | `intake_queue.py` | `intake_jobs` (the waiting‑room queue) |
| `ecb_rates.db` *(cache)* | `ecb_rates.py` | `ecb_fx` (reference FX) |

Separation keeps *who we are* (customers), *who they are* (suppliers), and *what
happened* (transactions/claims) cleanly apart; the transactional DB references master
data only by code.

---

## Request & background flow

- **Web request** (`app.py`): `before_request` sets the audit actor (thread‑local) and
  enforces the per‑endpoint capability; the handler renders server‑side HTML (escaped)
  with a vanilla‑JS asset served from `/app.js` (CSP‑safe). `after_request` adds security
  headers and resets the actor.
- **Background workers** (started by `serve.py` / `gunicorn_conf.py`, never on import):
  the **backup scheduler** (leader‑elected via `proclock` so only one process snapshots)
  and the **intake worker** (drains the waiting room; the queue claim hands each job to
  exactly one process, so every process can drain concurrently).

---

## Concurrency & scale

- **SQLite tuned for multiple processes** (`dbtune.py`): WAL + `busy_timeout` +
  `synchronous=NORMAL` on every connection, so several worker processes share the `.db`
  files without “database is locked”.
- **Cross‑process coordination** (`proclock.py`): a SQLite‑backed lease lock makes the
  scheduled backup a singleton and guards “one at a time” operations.
- **Audit actor** is thread‑local and resolved per row by a per‑connection SQL function
  (`ffs_actor()`), so concurrent requests never mis‑attribute a change.
- **Outgrowing SQLite?** `db.py` is the abstraction seam to PostgreSQL (the app logic,
  audit, and locks are unchanged; a real cutover also needs a paramstyle shim — see the
  honest note in `db.py`).

---

## The document vault

Originals are filed under a logical, backend‑agnostic tree so they can be located by
hand:

```
<Customer> <RegNo> / <Year> / <Country> / <Claim period Qn|Annual> / <file>
# onboarding/country docs:
<Customer> <RegNo> / customer-documents / <country|general> / <kind> / <file>
```

`doc_storage.py` builds that path once (`invoice_vault_path` / `customer_vault_path`) and
all backends store under it:

- **local** (default) — `documents/…`; locator is the path.
- **SharePoint** — Microsoft Graph; locator `sp://{drive}/{item}` (+ web URL).
- **FTP/FTPS** — stdlib `ftplib`, FTPS by default; locator `ftp://{remote path}`.

`get_bytes()` routes any locator by prefix, so history keeps resolving after a backend
switch. Every document carries a SHA‑256 for dedup and `verify_documents()` integrity
checks. When low‑VAT quarters merge into the annual claim, `file_documents_for_claim()`
re‑files exactly the documents of the invoices locked into that claim (DB‑safe move:
write new → repoint row → delete old).

---

## Conventions

- **Money** via `money.py` (Decimal, ROUND_HALF_UP); use `f2/fsum/q2`, never bare
  `round()` on currency. Storage columns stay SQLite `REAL`.
- **Prices** are NET EUR/L, final (VAT excluded, rebates applied). State this basis on
  any new report surface.
- **HTML** is escaped with `markupsafe.escape` (aliased `esc`) — never f‑string raw DB
  values into a page. The served JS is vanilla and CSP‑safe (`script-src 'self'`).
- **Schema migrations** are `try: ALTER TABLE … except sqlite3.OperationalError: pass`
  (column‑exists guard), guarded once per process by a `_SCHEMA_READY` set.
- **Audit**: every change is logged with `changed_by`; web requests set the actor via
  `audit.set_actor` / `reset_actor` in the request hooks.

---

## Testing

```bash
python -m pytest tests/ -q     # full suite (security, web, customers, claims, vault, queue, …)
python consolidate.py          # pipeline smoke test — must PASS every supplier
```

Inline smoke tests also exist in several modules (run the module directly, e.g.
`python doc_storage.py`, `python proclock.py`).
