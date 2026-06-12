# File index — what every file does

A plain‑language map of every module, grouped by the role it plays. The Python files
live flat in the repo root by design (each is location‑independent and imports its
siblings directly), so this index is how you navigate them. See
[ARCHITECTURE.md](ARCHITECTURE.md) for how they fit together.

## Web app & serving
| File | What it does |
|------|--------------|
| `app.py` | The Flask web application — ~25 pages, the JSON API, Excel exports, the request hooks (auth actor, security headers), and the background‑worker starters. |
| `serve.py` | Production launcher (waitress; Windows + Linux). Starts the app + the auto‑backup and intake background workers. HTTPS if a certificate is present. |
| `gunicorn_conf.py` | Gunicorn config for running several worker processes on Linux; its `post_fork` hook starts the background workers in each worker. |
| `queries.py` | Read‑only SQL aggregations used by the dashboard/compare/analytics pages (kept separate so they're easy to test). |
| `reports.py` | Polished Excel workbooks (executive summary, fee report/statement, claims readiness). |

## Authentication, audit & platform
| File | What it does |
|------|--------------|
| `auth.py` | Users, roles (admin/processor) and capabilities, scrypt password hashing, login lockout / IP throttle, app settings, error log. Owns `security.db`. |
| `audit.py` | Trigger‑based change history in every database (old/new snapshots + acting user); `history()`, `diff()`, `as_of()`. |
| `backup.py` | Crash‑consistent snapshots of all DBs + the document store + audit CSVs into `backups/`, each entry SHA‑256'd; `verify()`, `restore()`, rotation. |
| `db.py` | Database abstraction seam — SQLite by default, the documented path to PostgreSQL. |
| `db_tuning.py` | Applies WAL + busy_timeout + synchronous=NORMAL to every connection so multiple processes can share the `.db` files safely. |
| `process_lock.py` | Cross‑process advisory lease lock (SQLite) — makes the scheduled backup a singleton and guards “one at a time” operations. |
| `tls.py` | Builds the TLS context from a cert/key/chain or a PKCS#12 `.pfx` (env‑configurable). |
| `make_cert.py` | Generates a self‑signed certificate for internal use. |

## Intake (getting invoices in)
| File | What it does |
|------|--------------|
| `ingest.py` | Source adapters — read supplier data from xlsx / csv / xml / API. |
| `extract.py` | Turn a PDF/ZIP/**XML** batch into a reviewable *draft*: deterministic PDF parser, **UBL/CII e‑invoice parser** (EN 16931, 100% confidence), or AI backend (Claude/OpenAI/Azure). |
| `waiting_room.py` | The durable “waiting room” queue + background worker: park uploads, extract later one at a time, retry on token‑quota outages, hold for manual send. |
| `watch_inbox.py` | Optional folder watcher that auto‑extracts PDFs/ZIPs dropped into an inbox. |

## Engine (monthly pipeline)
| File | What it does |
|------|--------------|
| `consolidate.py` | Map raw supplier rows to the canonical schema and **tie them out to invoice totals**; refuses to build on a mismatch. Also the pipeline smoke test. |
| `validate.py` | Line‑level cross‑checks (VAT rate, signs, ranges, batch tie‑out) + a regression baseline; blocks commit on errors. |
| `build_master.py` | Builds the monthly master workbook (benchmark, comparison, stations, VAT view). |
| `history.py` | Loads a period into `fuel_history.db` (idempotent) and produces the trend report. Defines the `transactions` table. |
| `supplier_specs.py` | The trainable per‑supplier registry: row maps and validation targets. |
| `month_config.py` | The one file edited each month: period, input files, FX. |
| `vat_config.py` | Regulatory constants: goods codes, the 400/50 EUR minimums, deadline rule. |

## Master data
| File | What it does |
|------|--------------|
| `customer_master.py` / `customers.db` | Our entities (the refund applicants): registration/VAT, payout IBAN, portals, onboarding & per‑country activation, and the fee terms. |
| `supplier_master.py` / `suppliers.db` | Suppliers: legal identity, per‑country VAT registrations, banks, product catalogues, the invoice registry, statements, cadence. |

## VAT refunds, fees & compliance
| File | What it does |
|------|--------------|
| `vat_refund.py` / `vat_claims.db` | The heart: claims per entity × country × period, thresholds, one‑invoice‑one‑submission locks, the fee lifecycle, the document‑vault index, and the dynamic quarter→annual merge. Claim records live in their **own isolated database**; transactions are read from `fuel_history.db` via `analytics_connect()`. |
| `data_lake.py` | The data lake: AI‑processed extraction artifacts stored as files (same backends as the document vault), indexed in `data_lake.db`, readable by any module. |
| `invoice_control.py` | Receipt control (cadence × activity) and statement reconciliation with VAT triage (process / discard / discard‑domestic). |
| `contract_audit.py` | Contract‑compliance auditor: checks each invoiced line against the supplier's structured discount terms (`supplier_discounts`) and flags short discounts / over‑ceiling prices with the recoverable EUR. |
| `doc_mining.py` | Mines the vaulted documents (PDF text + XML) for EU VAT numbers and proposes fills for the INPUT gaps in supplier/customer master data. |
| `document_vault.py` | The document vault: the logical folder‑path builder and the storage backends — local / SharePoint / FTP(S) — plus integrity and re‑file helpers. |
| `pricing_intelligence.py` | Competitor NET‑price tracking and margin analysis against three baselines, plus a **self‑sourced benchmark** built from your own multi‑supplier purchases (best price achieved + avoidable overpay). |
| `portal_scraper.py` | Dynamic client‑portal price scraping: pluggable per‑supplier adapters that log into the entities' own authorized supplier portals and load NET prices into MY Prices. Credentials encrypted at rest (`portal.db`). |
| `anomaly.py` | Relative anomaly scan (station price vs country average, MoM jumps, volume spikes, off‑period dates). |
| `ecb_rates.py` / `market_prices.py` | Reference FX (ECB) and market fuel‑price pulls. |
| `money.py` | Decimal money helpers (ROUND_HALF_UP): `f2/fsum/q2`. Use these, never bare `round()` on currency. |

## Install, launch & utilities
| File | What it does |
|------|--------------|
| `setup_wizard.py` | First‑run guided setup; creates the admin, cert, first backup; re‑runnable, `--yes` for automation. |
| `start.py` + `start.sh` / `start.bat` / `start.command` | One‑click launch (no terminal): install deps, start the server, open the browser to setup. |
| `install.sh` / `install.bat` / `install_service_windows.ps1` | Guided installers / Windows service registration. |
| `cleanup.py` | Safely stops a running `app.py` (without matching itself, unlike `pkill`). |

## Sample / demo data generators (not part of the running app)
| File | What it does |
|------|--------------|
| `sample_build_q8.py`, `sample_build_bp.py`, `sample_build_dkv.py`, `sample_build_e100.py`, `sample_build_moeve.py`, `sample_build_tfc.py`, `sample_build_q8_full.py` | Generate the example supplier transaction workbooks used by the demo dataset (one per supplier). |
| `sample_dkv_data.py`, `sample_e100_data.py`, `sample_moeve_data.py` | The raw sample rows those builders use. |
| `sample_q8_adjust.py` | Produces the Q8 adjusted‑pricing sample workbook. |

## Tests
`tests/` — the pytest suite (web, security, auth, customers, claims, vault, intake
queue, multi‑process, reports, money, …) plus `conftest.py` fixtures. Run with
`python -m pytest tests/ -q`.
