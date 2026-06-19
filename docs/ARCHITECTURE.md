# Architecture map

A one-page orientation for safe changes: who owns what, which process writes which
database, how requests are authorized, and how background work flows. For the product
framing (the "seven delegated works") and the deep conventions, see `CLAUDE.md` and
`README.md`; this file is the structural quick-reference.

---

## 1. The two processes

The system is **one codebase, two roles** (`FFS_ROLE`):

- **Web tier** — `app.py` (Flask, ~25 pages + JSON API + Excel). Serves requests. Holds
  **no writable handle** to the engine-owned product DBs; it reads them **read-only**.
- **Worker / engine tier** — `python waiting_room.py --work` drains a durable queue, and
  `engine_close.py` runs the monthly close. This tier **owns and writes** the product DBs.

The boundary between them is the single most important invariant in the codebase.

---

## 2. Six technical blocks

| Block | Modules | Responsibility |
|---|---|---|
| **1 Intake** | `ingest.py`, `extract.py`, `waiting_room.py`, `portal_scraper.py`; advisory AI capture `vision_capture.py`→`ai_verify.py`→`capture_file.py` (+`capture_confidence.py`, `classify.py`) | raw files → review draft → enqueue |
| **2 Master data** | `customer_master.py`, `supplier_master.py`, `supplier_sync.py`, `vat_refund.py` | customers / suppliers / VAT registrations |
| **3 Engine** | `consolidate.py`→`validate.py`→`build_master.py`→`history.py`, orchestrated by `engine_close.py`; `metrics.py` | raw → validated transactions + settled aggregates |
| **4 Compliance** | `vat_refund.py`, `invoice_control.py`, `bank_recon.py` | claims, locks, receipt control, recon |
| **5 Presentation** | `app.py`, `pricing_intelligence.py`, `saft.py`, `finance.py`, `einvoice_export.py`, `workflow.py`, `ai_assistant.py`, DMS suite (`search`/`metadata`/`versioning`/`retention`/`sharing`/`esign`), `mcp_server.py` | pages, exports, sharing, MCP |
| **6 Platform** | `auth.py`, `audit.py`, `backup.py`, `tls.py`, `db.py`, `db_migrate.py`, `dataproduct.py`, `applog.py`, `keyvault.py`, `tenancy.py`, `process_lock.py`, `notify.py` | the floor under everything |

---

## 3. Database ownership & write boundaries

**Rule:** the **engine** owns and writes the product DBs; the **web app** reads them
read-only through `dataproduct.connect(...)` (a `mode=ro` SQLite URI — a stray app-side
write raises `sqlite3.OperationalError`). The app writes only its *own* DBs.

| Database | Written by | Read by app via | Tracked in git? |
|---|---|---|---|
| `fuel_history.db` (validated `transactions`, master, `settled_metrics`) | **ENGINE** | `dataproduct.connect("fuel_history")` (RO) | yes (demo) |
| `suppliers.db` (supplier master + VAT regs + brands) | ENGINE close **and** admin CRM in-request (`supplier_master.connect`) | `dataproduct.connect("suppliers")` (RO) | yes (demo) |
| `customers.db` (CRM) | app (`customer_master.py`) | direct | yes (demo) |
| `vat_claims.db` (claim lifecycle) | app (`vat_refund.py`) | direct | no (live) |
| `benchmark.db` (`my_prices`/`wholesale_prices`) | app/portal | direct | no |
| `security.db` (users, hashes, `app_settings`, `role_permissions`, `error_log`, `api_keys`, tenant registry) | app (`auth.py`) | direct | **no — never commit** |
| `intake.db` (waiting-room queue) | worker | — | no |
| `ecb_rates.db`, `portal.db`, `data_lake.db`, `import_log.db` | app/worker | direct | no |
| App-owned runtime DBs: `confidence`, `capture_confidence`, `finance`, `invoice_issue`, `sharing`, `esign`, `ai_chat`, `search`, `metadata`, `versions`, `retention`, `classify`, `workflow`, `locks` | their owning module | direct | no |

**Document vault** `documents/` (+ `captures/`, `data_lake/`) holds the live client PDFs —
**git-ignored, never commit** (CI enforces this via `tools/check_repo_hygiene.py`).

**Other write rules:**
- Statement registration is **enqueued** to the worker (`waiting_room` kind=`register`),
  never written in-request.
- All schema changes go through `db_migrate.apply(con, "<module>", [DDL,...])` —
  versioned, **append at the end** of a module's list.
- Every data change is **audit-logged** with `changed_by` (`audit.py`); web requests set
  the actor via `audit.set_actor`/`reset_actor` in app.py's before/after hooks.
- Secrets (portal credentials) are **envelope-encrypted** via `keyvault.py`.

---

## 4. Route groups & authorization

Every request passes `_guard()` (in `app.py`), which authorizes by **endpoint
classification**. Every registered endpoint must be in exactly one set, enforced by
`_assert_endpoint_coverage()` (logs by default; **`FFS_STRICT_ENDPOINTS=1` refuses to boot**
on an unclassified route — set in CI):

| Set | Meaning |
|---|---|
| `PERM_BY_ENDPOINT` | capability-gated (e.g. `data_import`, `pricing`, `vat_claims`); `processor` allowed if the role has the capability |
| `ADMIN_ONLY` | admin-only — the **entire VAT-refund module** and the **CRM** live here |
| `API_V1_SCOPE` | token-gated `/api/v1/*` (API keys, scoped, constant-time) |
| `OPEN_ENDPOINTS` | login/setup/static; the no-cookie token-as-principal routes (`share_*`, `sso_*`, `dokobit_postback`, `room_*`); intentionally login-only read pages |

`MODULES` are admin on/off switches over groups of endpoints (Admin panel). Roles:
`admin` (full) and `processor` (configurable capabilities, never server/user admin).

CSRF is enforced globally (per-session token); the only exemptions are the no-cookie
token-authority routes above.

---

## 5. Background worker flow

`waiting_room.py` is a **durable queue** (`intake.db`) drained by the worker tier. Job kinds:

- **`register`** — register an extracted statement into the VAT pipeline (actor propagated).
- **`fetch`** (`KIND_FETCH`) — automated portal capture; gated by a per-supplier
  rate-limiter / concurrency cap / backoff / circuit-breaker (`supplier_rate_limits`/
  `supplier_rate_state`, opt-in), auto-enqueued by an off-by-default scheduler.
- **`close`** (`KIND_CLOSE`) — the one-click monthly close.

The **monthly close** (`engine_close.py`) runs as an independent entrypoint:
`consolidate → build_master → history → run_control → backup`, `process_lock`-guarded,
single audit trail, period-stamped restartable pickle. Stage modules import side-effect-free
(`history.load`, `build_master.build` are functions). `metrics.py` settles dashboard
aggregates at the close (engine writes `settled_metrics`, app reads via `dataproduct`).

All fetching/closing runs **out-of-band** on the worker tier — never inline in a web request.

---

## 6. Deterministic-first extraction (capture path)

`extract.py` is deterministic-first: structured e-invoices (UBL/CII) and Factur-X/ZUGFeRD
embedded-XML parse with **no AI** at high confidence (`parse_einvoice`, before text/AI).
Untrusted XML is parsed via `safexml` (defusedxml) to block entity-expansion DoS. Only an
unstructured PDF with no registered parser falls to the AI backend, and only when one is
configured. The advisory **AI capture/verify** pipeline is the loudly-gated, opt-in,
default-OFF exception — it produces a draft a human still confirms; it mutates no figure.

---

## 7. CI & deployment

- **CI** (`.github/workflows/ci.yml`): repo-hygiene guard → `compileall` → `pytest`
  (with `FFS_STRICT_ENDPOINTS=1`) → `consolidate.py` smoke.
- **Dev-phase auto-deploy** (`deploy/`): the server polls the dev branch and self-updates
  (preserving the data DBs). See `deploy/AUTODEPLOY.md`. Disable before live production.

---

## Change-safety checklist

1. Writing a DB? Confirm the **owning process** (engine vs app) — never give the web app a
   writable handle to a product DB.
2. Adding a route? **Classify it** (`PERM_BY_ENDPOINT`/`ADMIN_ONLY`/`API_V1_SCOPE`/
   `OPEN_ENDPOINTS`) or CI (strict mode) fails.
3. Schema change? `db_migrate.apply(...)`, **appended** to the module's list.
4. Mutating data? Route it through the owning module + **audit log**; never `except: pass`.
5. New data/secret file? It must be git-ignored (CI hygiene guard enforces it).
6. Touching money? Use `money.py` (`f2`/`fsum`/`q2`), never bare `round()`.
