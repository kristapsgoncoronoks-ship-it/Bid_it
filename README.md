# Fleet Fuel & VAT Refund System

A self‑contained Flask web application for **fuel‑invoice processing, EU VAT refunds
(Directive 2008/9/EC), and competitor price intelligence**, built for five Baltic
transport entities and the agency that files their cross‑border VAT claims.

Everything lives in one folder — code, databases, the document vault, and reports —
so the folder *is* the unit of backup. It runs on a laptop for a single user or as
several worker processes behind a proxy for a team, with no change of code.

---

## What it does

| Area | Capabilities |
|------|--------------|
| **Intake** | Upload PDF/ZIP/XML invoice batches; deterministic PDF parser (offline), **UBL/CII e‑invoice parsing** (EN 16931, 100% confidence), or AI extraction (Claude/OpenAI/Azure). A durable **“waiting room” queue** parks uploads and processes them in the background so bursts never overload the server. |
| **Master data** | Separate SQLite databases — our entities (`customers.db`), suppliers (`suppliers.db`), transactions (`fuel_history.db`), and the **VAT‑refund claim records isolated in their own `vat_claims.db`** so the monthly rebuild can't corrupt them. AI‑processed extraction output is archived in a **data lake** (same storage backends as the PDF vault). |
| **Engine** | Consolidate → validate (tie‑out to invoice totals) → build monthly master workbook → load history/trend. |
| **VAT refunds** *(admin‑only module)* | Claims per **entity × country × period** (Q1–Q4 or annual), 400/50 EUR thresholds, one‑invoice‑one‑submission locks, claim packs. A **controlled status workflow 1A→5**: the pre‑submission stages (1A missing docs · 1B period not ended · 1C/1E ready) are **derived by the system from an adjustable checklist** (contract, customer data, bank account, NACE, trade register, power of attorney) with a **hard period‑end gate**; then 2 submitted → 2B document request → 3 decision → 3A money → 3B rejection / 3D appeal / 3C confiscation (locks kept) → 4/4A invoice fee/credit → 5 closed. Low‑VAT quarters **merge dynamically** into the annual claim. Claims are built **from registered invoices** — every line ties to one invoice, **one row per product code** (Art. 9 codes), nothing synthetic filed; figures stay editable but guarded against accidental change. |
| **Customer CRM** | The Customers page is a mini‑CRM: onboarding + per‑country activation, the adjustable checklist rules, **document generation from your own templates** ({{placeholders}} filled with customer data; .txt/.html/.md/.docx, optional PDF), document **validity dates** (expired POA re‑blocks claims), fee terms & payout routing. |
| **Service fees** | % of refunded VAT floored at a per‑declaration minimum; per‑customer/per‑country overrides; rate frozen at submission, charged at payout; fee invoice + settlement. |
| **Price intelligence** | Competitor NET‑price tracking and margin analysis, a **self‑sourced benchmark** from your own multi‑supplier purchases (best price achieved + avoidable overpay), and a **dynamic client‑portal scraper** (encrypted credentials). |
| **Compliance** | Receipt control (cadence × activity), statement reconciliation with VAT triage, a **contract‑compliance auditor** (catches short discounts / over‑ceiling prices to claw back), **document mining** (auto‑fills INPUT gaps from the vault), anomaly scan, full audit trail. |
| **Document vault** | Originals stored under a logical, human‑navigable tree — `Customer (reg no) / Year / Country / Claim period / file` — identical across **local / SharePoint / FTP(S)** backends; SHA‑256 dedup + integrity verification. A doc‑missing claim is resolved in place: **upload**, or **search the store and attach an already‑stored file** (same dedup + wrong‑attachment check). |
| **Platform** | Roles (admin/processor; the VAT module is admin‑only), **admin on/off switches for whole app parts**, login lockout & IP throttle, CSP/security headers, scheduled backups with **document‑integrity checks** and an **admin error log** (every failure recorded for review), TLS, versioned schema migrations + a logging layer. |
| **Scales with you** | One laptop → a team behind a proxy → a multi‑server fleet, by **configuration, not rewrite**: node roles (`FFS_ROLE` web/worker), shared signed‑cookie sessions (`FFS_SECRET_KEY`, no sticky sessions), pluggable storage (local / SharePoint / FTPS), a lease‑based queue + leader‑elected scheduler, and a **SQLite→PostgreSQL** database abstraction. See **[docs/SCALING.md](docs/SCALING.md)**. |

---

## Get started in 3 steps

No terminal, no config files — just double‑click and follow the screen.

1. **Install Python once** (only if you don't have it). Get it free from
   [python.org/downloads](https://www.python.org/downloads/). On Windows, tick
   **“Add Python to PATH”** during install.
2. **Double‑click the launcher for your computer:**

   | Windows | macOS | Linux |
   |---------|-------|-------|
   | `start.bat` | `start.command` | `start.sh` |

   The first run installs everything it needs (about a minute) and opens your browser
   automatically.
3. **Follow the welcome page.** It asks you to pick an admin username and password —
   that's the whole setup. A green “You're all set” screen sends you to sign in.

That's it. Leave the small console window open while you work; close it (or press
`Ctrl+C`) to stop the app.

> **If your computer blocks the launcher** (some macOS/Windows security prompts), right‑click
> it → **Open** once, or run `python start.py` from a terminal in this folder. Same result.

<details>
<summary><b>Prefer the command line?</b></summary>

```bash
pip install -r requirements.txt          # flask, openpyxl, waitress, cryptography, pypdf (+ requests)
python start.py                          # installs deps if needed, opens the browser, runs setup
# or run the pieces directly:
python app.py                            # dev server  → http://localhost:8050
python serve.py                          # production (waitress; Windows + Linux), HTTPS if a cert is present
```

A guided terminal installer is also available: `./install.sh` (Linux/macOS) or
`install.bat` (Windows) — it checks Python, installs packages, makes a certificate,
creates the admin account, and runs a self‑check. Add `--yes --user admin --password '…'`
for unattended IT setup.

</details>

> Full step‑by‑step **server** install (Ubuntu **and** Windows, TLS, systemd, nginx,
> backups, multi‑process) is in **[docs/INSTALL.md](docs/INSTALL.md)**.

---

## Documentation

| Guide | What's in it |
|-------|--------------|
| **[docs/INSTALL.md](docs/INSTALL.md)** | Full server setup — one‑click, Ubuntu service (systemd), Windows service, TLS, nginx proxy, multi‑process (gunicorn/waitress), backups. |
| **[docs/USER_MANUAL.md](docs/USER_MANUAL.md)** | How to work with the system day‑to‑day — every page, the monthly routine, VAT refunds, the waiting room, the vault. |
| **[docs/PLATFORM.md](docs/PLATFORM.md)** | The product lens — the system as an accounting platform of **seven delegated works** (data processing, analytics/export, document storage, light CRM w/ API‑plugin seam, VAT processing, VAT control, invoicing‑for‑work), each with owner module, maturity and gap. |
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | The six building blocks, the data model, the module map, and the key conventions. |
| **[docs/DIAGRAMS.md](docs/DIAGRAMS.md)** | Visual schematics — system overview, upload OK/Bad flow, monthly close, VAT claim lifecycle, **claim composition & document resolution**, databases, storage, request/worker flow, multi‑server topology, and the backup & integrity self‑control loop. |
| **[docs/SCALING.md](docs/SCALING.md)** | Horizontal scaling — the ladder (tune → Postgres → offload storage → worker fleet → load‑balance), target topology, env‑var reference, and the honest remaining blockers to a validated Postgres cutover. |
| **[docs/ROADMAP.md](docs/ROADMAP.md)** | How the system should evolve to support the business — outcome‑driven plan across three horizons, with KPIs. |
| **[docs/VAT_REFUND_RULES.md](docs/VAT_REFUND_RULES.md)** | The Directive 2008/9/EC compliance reference — verified parameters (thresholds, deadline, periods), expenditure codes, country diesel recoverability, and a cross‑check against what the code encodes. |
| **[docs/FILE_INDEX.md](docs/FILE_INDEX.md)** | A plain‑language index of what every file in the repo does. |
| **[SECURITY.md](SECURITY.md)** | Security model, data protection, and the DPA basis for AI extraction. |
| **[docs/GIT_SETUP.md](docs/GIT_SETUP.md)** | Cloning, branching, and what is / isn't committed. |

---

## Roles

- **admin** — everything, including the **VAT‑refund module** (claims, readiness,
  recovery & fees, customer CRM), server setup, user administration, editing
  processor capabilities, and **switching whole app parts on/off** (Admin → Modules).
- **processor** — day‑to‑day work (import, compliance, analytics, documents, exports).
  Capabilities are configurable by an admin; a processor can never see the VAT‑refund
  module or do server/user administration.

Authorization is enforced centrally per endpoint (`auth.has_perm` / `PERM_BY_ENDPOINT`,
plus the `ADMIN_ONLY` set and the module on/off switches).

---

## Repository layout

The Python modules are deliberately **flat** (siblings in the repo root): every module
is location‑independent (`WORKDIR = os.path.dirname(os.path.abspath(__file__))`) and
imports its siblings directly, and each `connect()` opens `<name>.db` next to the code.
This keeps the whole system a single, copy‑anywhere folder. The files group logically:

```
fleet_fuel_system/
├── app.py serve.py            # web app (≈25 pages + JSON API + Excel) and prod launcher
├── auth.py audit.py           # users/roles/login + trigger-based change history
├── db.py db_tuning.py process_lock.py  # SQLite→Postgres abstraction, WAL/busy-timeout tuning, cross-process lease
├── db_migrate.py applog.py    # versioned schema migrations (run once per DB) + logging layer
├── backup.py tls.py make_cert.py  # snapshots+integrity, TLS context, self-signed certs
│
├── ingest.py extract.py       # source adapters (xlsx/csv/xml/api); PDF/ZIP → draft
├── waiting_room.py            # durable "waiting room" queue + background worker
├── consolidate.py validate.py # map to canonical schema + tie-out; blocks on errors
├── build_master.py history.py # monthly master workbook; load + trend into fuel_history.db
├── supplier_specs.py month_config.py vat_config.py  # registries / monthly + regulatory config
│
├── customer_master.py customers.db   # our entities (reg, VAT, payout IBAN, activation, fees)
├── supplier_master.py suppliers.db   # suppliers (VAT regs, banks, products, invoice registry)
├── vat_refund.py fuel_history.db # claims, locks, fees, document vault index
├── invoice_control.py            # receipt control + statement reconciliation/triage
├── document_vault.py                # vault backends: local / SharePoint / FTP(S)
├── pricing_intelligence.py anomaly.py reports.py  # price intelligence, anomaly scan, Excel reports
│
├── setup_wizard.py start.* install.*  # first-run wizard + one-click launchers
├── gunicorn_conf.py                   # multi-process worker config (Linux)
├── documents/                  # the document vault (local backend; git-ignored content)
├── tests/                      # pytest suite (240+ tests)
└── docs/                       # INSTALL · USER_MANUAL · ARCHITECTURE · DIAGRAMS · PLATFORM · SCALING · ROADMAP · VAT_REFUND_RULES · FILE_INDEX · GIT_SETUP
```

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the full module map and data model.

---

## The runbooks (summary)

**Monthly close** — drop supplier files → edit `month_config.py` → `python consolidate.py`
(must PASS every supplier vs its invoice totals) → `python build_master.py` →
`python history.py` → `python invoice_control.py <period>` → register statements & attach
documents in the UI → `python backup.py`.

**Quarterly VAT refunds** — after quarter end, review **Claims** (readiness) → for each
READY stream confirm every invoice has its original in the vault → file via the entity's
home portal → set status **submitted** in the UI (this *locks* the invoices). Low‑VAT
quarters defer into the **annual** claim; the vault re‑files those documents into the
year's `Annual` folder automatically. Deadline: 30 September of the following year.

Step‑by‑step instructions for both are in **[docs/USER_MANUAL.md](docs/USER_MANUAL.md)**.

---

## Testing

```bash
python -m pytest tests/ -q        # the full suite
python consolidate.py             # the pipeline smoke test (must PASS all suppliers)
```

---

## Conventions (for contributors)

- Prices everywhere are **NET EUR/L, final** (VAT excluded, rebates applied).
- Money is quantized via `money.py` (Decimal, ROUND_HALF_UP) — never bare `round()` on currency.
- HTML output is escaped with `markupsafe.escape` — never f‑string raw DB values into a page.
- Every data change is audit‑logged with the acting user.
- Do **not** commit secrets (`.secret_key`, certs), `security.db`, generated Excel, or
  runtime dirs (`backups/`, `inbox/`). See `.gitignore`.

More detail for AI/codebase contributors is in `CLAUDE.md`.
