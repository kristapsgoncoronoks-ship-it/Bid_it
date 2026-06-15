# Architecture

This document describes how the Fleet Fuel & VAT Refund System is put together: the
six building blocks, the data model, the module map, and the conventions that keep it
consistent. For **visual schematics** of every flow see **[DIAGRAMS.md](DIAGRAMS.md)**;
for setup see **[INSTALL.md](INSTALL.md)**; for day‑to‑day use see
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
                   waiting_room.py (durable "waiting room" + background worker)
        │
2. MASTER DATA     customers.db (customer_master.py)   suppliers.db (supplier_master.py)
                   fuel_history.db (vat_refund.py / history.py)
        │
3. ENGINE          consolidate.py → validate.py → build_master.py → history.py →
                   metrics.py (per-period aggregates), orchestrated by engine_close.py
        │
4. COMPLIANCE      vat_refund.py (claims, locks, fees, vault index),
                   invoice_control.py (receipt control / statement triage),
                   contract_audit.py (discount-terms compliance),
                   doc_mining.py (fill INPUT gaps from the vault),
                   bank_recon.py (advisory bank↔refund reconciliation)
        │
5. PRESENTATION    app.py (Flask: ~25 pages + JSON API + Excel/CSV/SAF-T), pricing_intelligence.py,
                   reports.py, anomaly.py, saft.py (SAF-T export), finance.py (advisory finance),
                   confidence.py (advisory-AI trust)
        │
6. PLATFORM        auth.py, audit.py, backup.py, tls.py, document_vault.py, db.py,
                   db_tuning.py, process_lock.py, keyvault.py (envelope-encrypted secrets),
                   tenancy.py (multi-tenant foundation, OFF by default)
```

---

## Data model (three databases, separated on purpose)

| Database | Owner module | Key tables |
|----------|--------------|------------|
| `customers.db` | `customer_master.py` | `customers` (reg/VAT/payout/fee), `customer_bank_accounts`, `customer_documents`, `customer_countries` (per‑country activation), `customer_fees`, `country_requirements` |
| `suppliers.db` | `supplier_master.py` | `suppliers` (+ cadence), `supplier_vat_registrations`, `supplier_bank_accounts`, `supplier_products`, `supplier_invoices`, `supplier_statements` |
| `fuel_history.db` | `history.py`, `invoice_control.py`, `metrics.py` | `transactions` (canonical fuel lines, **rebuilt monthly**), `invoice_receipt_control`, `settled_metrics` (per‑period dashboard aggregates, materialized at the close) |
| `vat_claims.db` *(isolated)* | `vat_refund.py` | `vat_applications` (claim lifecycle + fees), `vat_claimed_invoices` (one‑invoice‑one‑submission locks), `invoice_documents` (vault index, SHA‑256). **Kept in its own file so the monthly transaction rebuild can never corrupt the legal/financial claim records;** transactions are read from `fuel_history.db` on demand (`analytics_connect()`). |
| `data_lake.db` + `data_lake/` *(not committed)* | `data_lake.py` | index of AI‑processed extraction artifacts; the files themselves go through the same storage backends as the document vault |
| `security.db` *(not committed)* | `auth.py`, `tenancy.py` | `users` (scrypt hashes), `role_permissions`, `login_log`, `error_log`, `app_settings`, the **tenant registry** (app‑owned platform metadata, kept inside the security‑DB backup/permission envelope) |
| `intake.db` *(operational)* | `waiting_room.py` | `intake_jobs` (the waiting‑room queue — kinds `extract`/`register`/`close`/`fetch`), `supplier_rate_limits` / `supplier_rate_state` (the per‑supplier rate‑limiter / circuit‑breaker) |
| `portal.db` *(secrets, not committed)* | `portal_scraper.py` | `portal_configs`, `portal_credentials` (envelope‑encrypted via `keyvault.py`), `portal_runs` |
| `confidence.db` *(runtime, app‑owned)* | `confidence.py` | per‑(supplier × country) trust score + the validation‑event ledger (governs only whether the advisory AI review runs) |
| `finance.db` *(runtime, finance‑owned)* | `finance.py` | the advances ledger for the embedded‑finance seam (NullProvider default → records advance *intent* only) |
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
  the **backup scheduler** (leader‑elected via `process_lock` so only one process snapshots)
  and the **intake worker** (drains the waiting room; the queue claim hands each job to
  exactly one process, so every process can drain concurrently). The waiting room carries
  four job **kinds** — `extract` (parse an upload), `register` (statement registration on
  the engine), `close` (the one‑click monthly close), and `fetch` (a portal pull).
- **Automated document capture** runs entirely on the worker tier — *never inline in a web
  request*. A portal pull is enqueued as a `fetch` job; before it runs, the **per‑supplier
  rate‑limiter / concurrency cap / backoff / circuit‑breaker** (`supplier_rate_limits` /
  `supplier_rate_state`, opt‑in — an ungoverned supplier behaves exactly as before) decides
  whether to start it. An **off‑by‑default scheduler** (`scrape_scheduler_enabled`)
  auto‑enqueues pulls. The worker calls `portal_scraper.scrape`, which reads the supplier's
  credentials sealed by `keyvault.py` (envelope encryption — a per‑secret AES‑256‑GCM data
  key wrapped by a pluggable KEK; `keyvault_provider` selects `local` or a BYOK/KMS `env`
  key from `FFS_KEK_KEY`). The capture flow is therefore:
  *enqueue → rate‑limiter gate → `fetch` worker → `portal_scraper` → `keyvault` creds → draft.*
- **Metrics at the close**: the dashboard KPIs and the avoidable‑overpay figure are
  otherwise recomputed live on every load. `metrics.rebuild(period)` settles them once at
  the close into `settled_metrics` (ENGINE‑owned `fuel_history.db`, written exactly as
  `history.py` writes; the app only *reads* it via the read‑only `dataproduct` window). The
  figures come from the canonical `queries.py` functions — not a fork — and `verify()`
  recompute‑compares (a drift check). An un‑rebuilt period still renders via the live fallback.

---

## Concurrency & scale

- **SQLite tuned for multiple processes** (`db_tuning.py`): WAL + `busy_timeout` +
  `synchronous=NORMAL` on every connection, so several worker processes share the `.db`
  files without “database is locked”.
- **Cross‑process coordination** (`process_lock.py`): a SQLite‑backed lease lock makes the
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

`document_vault.py` builds that path once (`invoice_vault_path` / `customer_vault_path`) and
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
- **Schema migrations** go through `db_migrate.apply(con, "<module>", [DDL, …])` — a
  versioned migration table (`_ffs_migrations`) records what has run, so each `ALTER`
  executes **once per database** instead of being retried on every connect. Append new
  statements at the END of a module's list (positions are stable).
- **Audit**: every change is logged with `changed_by`; web requests set the actor via
  `audit.set_actor` / `reset_actor` in the request hooks.

---

## Testing

```bash
python -m pytest tests/ -q     # full suite (security, web, customers, claims, vault, queue, …)
python consolidate.py          # pipeline smoke test — must PASS every supplier
```

Inline smoke tests also exist in several modules (run the module directly, e.g.
`python document_vault.py`, `python process_lock.py`).
