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
   AI capture pipeline (OPT-IN, default-OFF, advisory, sends PDF page-images to the configured AI):
   `vision_capture.py` (vision→structured capture doc), `ai_verify.py` (verify capture vs PDF + apply
   corrections; INDEPENDENT verify model/provider), `capture_file.py` (persist capture as a JSON+text
   second file), `capture_confidence.py` (per-supplier×field accuracy learning loop), `classify.py`
   (data-classification/DLP gate over external AI)
3. Engine     `consolidate.py`→`validate.py`→`build_master.py`→`history.py`, orchestrated by `engine_close.py`
4. Compliance `vat_refund.py` (claims, locks), `invoice_control.py` (receipt/triage), `bank_recon.py` (advisory recon)
5. Presentation `app.py` (Flask, ~25 pages + JSON API + Excel), `pricing_intelligence.py`, `saft.py`, `finance.py`
               (deepened embedded-finance origination), `einvoice_export.py` (EN-16931/UBL 2.1 export hub),
               `workflow.py` (advisory approval/routing engine), `ai_assistant.py` (advisory chat over derived data);
               document-management + sharing suite `search.py`/`metadata.py`/`versioning.py`/`retention.py`,
               `sharing.py`/`share_watermark.py`/`esign.py`; `mcp_tools.py`+`mcp_server.py` (read-only MCP server)
6. Platform   `auth.py`, `audit.py`, `backup.py`, `tls.py`, `document_vault.py`, `db.py`, `dataproduct.py`,
               `db_migrate.py`, `applog.py`, `data_lake.py`, `doc_storage.py`, `notify.py`, `process_lock.py`,
               `keyvault.py` (envelope-encrypted secrets), `tenancy.py` (multi-tenant foundation), `metrics.py` (close-time aggregates)

## Platform capabilities — seven delegated works (the product lens)
The six blocks are the *technical* decomposition; read the product as an **accounting
platform of seven delegated works**, each owned by a module (and mostly its own DB):
1. **Data processing** — `ingest.py`/`extract.py`/`waiting_room.py` (incl. automated portal capture, KIND_FETCH) → `consolidate→validate→build_master→history` (raw files → validated, reconciled transactions); `metrics.py` settles per-period dashboard aggregates at the close. Optional advisory **AI capture pipeline** (`vision_capture.py`→`ai_verify.py`→`capture_file.py`, learning via `capture_confidence.py`, DLP-gated by `classify.py`) — all default-OFF; a human still confirms.
2. **Invoice analytics & report export** — `pricing_intelligence.py`, `anomaly.py`, `contract_audit.py`, `confidence.py` (advisory-AI trust), `reports.py` + the Excel/CSV/SAF-T exports (`saft.py`) + `einvoice_export.py` (EN-16931/UBL 2.1 outbound XML + the Accounting & ERP exports hub); `ai_assistant.py` (advisory chat over derived data); `mcp_tools.py`/`mcp_server.py` expose read-only data to AI agents.
3. **Digital document storage** — `document_vault.py` (local/SharePoint/FTPS), `data_lake.py`, `doc_storage.py`; SHA-256 dedup + `verify_documents`; portal credentials sealed via `keyvault.py`. DMS overlays (app-owned, keyed by `doc:<id>`): `search.py` (FTS5), `metadata.py` (typed fields+tags), `versioning.py`, `retention.py` (+ legal hold), `classify.py` (sensitivity labels/DLP); secure sharing `sharing.py`/`share_watermark.py`/`esign.py` (links, NDA/watermark, page analytics, data rooms, SES e-sign); `workflow.py` (advisory approval/routing).
4. **Light CRM (API-plugin scalable)** — `customer_master.py` (entities, activation, checklist rules, templates, fees, expiry); extensibility seam = the `extract.py` parser registry / `portal_scraper.py` adapters / `/api/*`.
5. **VAT processing** — `vat_refund.py` claim lifecycle (1A→5), `vat_config.py`, claim workbook; `finance.py` (advisory embedded-finance ORIGINATION/modeling over the receivable — financeable receivables, per-claim advance/fee offers, advances ledger; NullProvider default, never moves money).
6. **VAT control** — `invoice_control.py` (receipt control / reconciliation), `bank_recon.py` (advisory bank↔refund recon) + the submission gates (checklist, doc-presence, locks, period-end).
7. **Invoicing for work** — the service-fee engine in `vat_refund.py` + `reports.fee_report_workbook` (Recovery page).
Platform floor under all seven: `auth`/`audit`/`backup`/`db`/`db_migrate`/`applog`/`tls`/`process_lock`/`keyvault`/`tenancy`. See `README.md#the-platform-seven-delegated-works`.

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
  live in `benchmark.db` (app/portal-owned). See `README.md#the-platform-seven-delegated-works`.
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
  or gates a figure; see `docs/MANUAL.md#ai-review-assistant-advisory-validation-analytics`).
- AI CAPTURE pipeline is the deliberate, LOUDLY-GATED EXCEPTION to deterministic-first capture:
  `vision_capture.py` renders the ORIGINAL PDF to page-images and sends them to a VISION model
  (Claude/OpenAI) → a structured "capture document" mapped into the SAME review-draft shape. It is
  OPT-IN/default-OFF (`ai_vision_capture_enabled` AND a vision backend), ADVISORY (a draft a human
  still confirms — mutates no figure/DB), STRICT (never invents a field), and best-effort (falls
  back to the OCR→parser→text-AI chain). `ai_verify.py` is the INDEPENDENT verify model/provider:
  it verifies the captured draft field-by-field against the PDF (PDF = source of truth), can APPLY
  PDF-authoritative corrections then re-verify — but NEVER auto-changes/gates a figure without the
  human confirm gate (default-OFF `ai_verify_enabled`). `capture_file.py` persists the capture as a
  permanent JSON+text SECOND FILE in the data lake (kind `capture_document`), linked to the upload
  SHA-256, newest-wins, never the PDF/secret bytes. `capture_confidence.py` is the per-(supplier×
  field) ACCURACY LEARNING LOOP (own `capture_confidence.db`) — advisory hints from correction/edit
  signals, NEVER gates. `classify.py` is the data-classification/DLP overlay (own `classify.db`):
  per-document sensitivity label + `{type,count}` findings (NEVER raw values); an OPT-IN policy
  (`ai_external_max_sensitivity`, default `restricted` = PERMISSIVE) blocks over-sensitive docs from
  external AI, fails OPEN on a scan error / CLOSED when a policy is set and exceeded.
- MCP server (`mcp_server.py` over `mcp_tools.py`) exposes platform data to AI agents: READ-ONLY
  (v1, no write/action tools), reads product DBs strictly via `dataproduct.connect` (ro), NEVER
  raises (returns `{"error":...}`), tenant-aware, NO bank/secret data (safe-column selects +
  defense-in-depth key filter). The `mcp` SDK is an OPTIONAL extra (`requirements-mcp.txt`) imported
  ONLY inside `mcp_server` — the app/tests never need it. stdio transport is trusted; streamable-HTTP
  REQUIRES a bearer token (`FFS_MCP_TOKEN` or an `api_keys` token). See `docs/MCP.md`.
- `workflow.py` (own `workflow.db`) is an ADVISORY, configurable approval/routing engine — ordered
  steps (approve/sign/notify/tag), a Tasks/Approvals inbox. It is structurally separate from the
  refund engine and NEVER overrides a VAT legal gate (checklist/locks/period-end/claim status); a run
  reaching `approved` changes nothing about a claim. Side-effect steps reuse existing advisory seams
  (`esign`/`notify`/`metadata`), best-effort.
- `einvoice_export.py` is the OUTBOUND counterpart to `extract.parse_einvoice`: EN-16931/UBL 2.1
  Invoice XML EXPORT (+ batch ZIP) of REGISTERED invoices, READ-ONLY over `vat_refund.invoice_lines`,
  NET-EUR via `money.f2` — invents no figure, writes no product DB.
- Automated document capture runs OUT-OF-BAND on the worker tier, never in a web request.
  Portal fetch is enqueued as `waiting_room` kind=`fetch` (`KIND_FETCH`); a per-supplier
  rate-limiter / concurrency cap / backoff / circuit-breaker gates it (`supplier_rate_limits`
  /`supplier_rate_state`, OPT-IN — an ungoverned supplier behaves exactly as before), and an
  OFF-by-default scheduler (`scrape_scheduler_enabled`) auto-enqueues pulls. The one-click
  monthly close is likewise enqueued as kind=`close` (`KIND_CLOSE`).
- Stored secrets (portal credentials) use ENVELOPE encryption via `keyvault.py`: a fresh
  AES-256-GCM DEK per secret, wrapped by a KEK and AAD-bound to its context. KEK provider is
  pluggable via `keyvault_provider` (default `local` = derived from the app secret; `env` =
  `EnvKEK`, BYOK 32-byte key from `FFS_KEK_KEY` / per-tenant `FFS_KEK_KEY_<TENANT>`, fails
  LOUD if missing). Switching the provider on a populated store needs a re-wrap migration.
  Never log a plaintext secret; GCM auth failures RAISE — never silently return "".
- `confidence.py` (per-supplier×country trust) governs ONLY whether the advisory AI review
  RUNS — it never skips or alters a deterministic legal gate; it fails toward doing the review.
- `finance.py` (embedded finance) and `bank_recon.py` (open-banking recon) are ADVISORY/
  origination-only seams: additive analytics over `recovery_report()` with a NullProvider
  default; they NEVER mutate a VAT figure, status, lock, fee, or payment. `finance.py` is
  deepened into an ORIGINATION/modeling feature — `financeable()` reuses the recovery
  `outstanding` total, `financeable_offers()`/`offer_for` model per-claim advance/fee/net-now/
  net-later economics, and an advances ledger (own `finance.db`) TRACKS an advance through
  {offered,accepted,funded,repaid,declined}; with the NULL provider nothing funds, no money moves.
- Multi-tenancy (`tenancy.py`) is OFF by default (`multitenant` setting) = byte-identical
  single-tenant; `scope_clause()`/`require_tenant()` are inert no-ops until the per-table
  phase. Registry lives in `security.db`. See `docs/STRATEGY.md#multi-tenancy-program-plan`.
- `metrics.py` materializes dashboard aggregates (`settled_metrics` in the ENGINE-owned
  `fuel_history.db`) at the close — ENGINE writes, app READS via `dataproduct.connect`; the
  figures come from canonical `queries.py` (not forked) and `verify()` recompute-compares
  (drift check). An un-rebuilt period still renders via the live fallback.
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
  10 other — NEVER 9 = luxuries/entertainment). See `docs/MANUAL.md#eu-cross-border-vat-refund-rules-reference-directive-20089ec`.
- NET/effective price = `net_eur_eff / qty`. City dimension = the `station` column.

## Do NOT commit
Secrets (.secret_key, certs), `security.db` (password hashes), generated Excel,
runtime dirs (backups/, inbox/, **documents/** = the live vault of client invoice PDFs,
captures/, data_lake/), and every app-owned runtime DB — including this session's
new ones (already in `.gitignore`): `capture_confidence.db`, `classify.db`, `workflow.db`,
`sharing.db`, `esign.db`, `ai_chat.db`, `search.db`, `metadata.db`, `versions.db`,
`retention.db`, `finance.db` (vision/verify add no DB beyond `capture_confidence.db`). See `.gitignore`.

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

## Strategic direction (see `docs/STRATEGY.md`)
The product thesis: **turn a transport company's messy, multi-supplier fuel/toll spend into
recovered cash and an audit-ready financial record — across every fuel card, automatically —
and own the cash-timing of the refund by financing it.** Monetisation roadmap (priority order):
direct-to-fleet recovery (multi-card wedge) → embedded finance (factor the VAT receivable via a
licensed partner) → expense reports + SAF-T/e-invoice/ERP export (ViDA tailwind 2026-2030) →
open-banking reconciliation/pay-by-bank (aggregator/agent partner) → pooled benchmark
(internal-first, counsel-gated to externalise). The moat is the proprietary, multi-network,
line-item invoice dataset. Full analysis + the four monetisation models in `docs/STRATEGY.md`.

**Data-acquisition direction (automated document capture).** Build BOTH paths — supplier APIs /
e-invoicing inbound (PEPPOL/EN-16931, mandatory under ViDA) where a supplier supports it, AND
credential-based portal scraping (`portal_scraper.py` adapters) for the majority of low-IT
suppliers that offer neither. Run all fetching OUT-OF-BAND on the intake queue + a dedicated
worker tier (`FFS_ROLE`, `python waiting_room.py --work`) with per-supplier rate-limit /
concurrency caps / backoff / circuit-breaker — never inline in a web request. Stored portal
credentials MUST use envelope encryption (KEK→DEK) backed by KMS/HSM, per-tenant/BYOK keys (so
the platform can't bulk-decrypt one tenant's secrets), prefer OAuth/scoped tokens over passwords
where available, least-privilege + full audit. If the product goes multi-CLIENT SaaS,
tenant-isolation becomes first-class (tenant-scoped enforcement at every query; a cross-tenant
leak is a GDPR Art. 33/34 breach). Target SOC 2 Type II + ISO 27001/27017/27018.

## Known next steps (backlog) — canonical live list in `docs/STRATEGY.md#backlog`
`docs/STRATEGY.md#backlog` is the authoritative, current backlog (ready-now / decision-gated / strategic /
platform). Recently SHIPPED (no longer open): the money-precision sweep, the whole
`except: pass`→`applog` migration, `.docx`→PDF generation (document module), DLQ alerting +
oldest-pending SLO, the register-failure reconcile, the §B VAT-correctness work (national-currency
threshold hard-gate, receipt-control gate+waive, FX provenance + per-invoice ECB verification),
the CRM integration API, the full document-management module; and this session: the automated-
capture worker tier (`KIND_FETCH` + per-supplier rate-limiter/circuit-breaker + off-by-default
scheduler) with envelope-encrypted credential custody (`keyvault.py`); one-click monthly close
(`KIND_CLOSE`, `/close`); per-event + deadline alerts + SMTP relay UI; off-machine backup sync;
metrics-at-close materialization + drift check (`metrics.py`); the strategy-derived seams —
expense/cost reports + accounting-ledger CSV + SAF-T export (`saft.py`), advisory embedded finance
(`finance.py`) and open-banking reconciliation (`bank_recon.py`); confidence-learning
(`confidence.py`); and the multi-tenancy foundation (`tenancy.py`, OFF by default); the full
document-management + secure-sharing platform (search/metadata/versioning/retention; sharing/
watermark/page-analytics/data-rooms/e-sign — see `docs/STRATEGY.md` §10); and THIS session: the
advisory AI capture→verify→correct pipeline (`vision_capture.py`/`ai_verify.py`/`capture_file.py`/
`capture_confidence.py`, all default-OFF) with a data-classification/DLP gate (`classify.py`); the
read-only token-gated MCP server (`mcp_server.py`/`mcp_tools.py`, optional SDK, `docs/MCP.md`); the
advisory workflow/approval engine (`workflow.py`); EN-16931/UBL 2.1 e-invoice + ERP export
(`einvoice_export.py`); the deepened embedded-finance origination model (`finance.py`); the advisory
document chat (`ai_assistant.py`); the nav-IA cleanup (Home · Intake · Documents · Sharing ·
Analytics · VAT & Recovery · Master data · History · Export · Admin); and a deterministically-green
test suite (a conftest fixture isolates `app_settings` + `role_permissions` per test). Still open:
- **Automated document capture (go-live)** — the scaffolding (worker fetch, rate-limiter,
  credential custody, scheduler) has landed; remaining = REAL per-supplier `portal_scraper`
  adapters + live API/e-invoicing inbound, and KMS/OAuth/per-tenant-BYOK custody beyond the
  `env`/local KEK seam. The flagship near-term build.
- **D6** — intake worker as a dedicated worker-process by default (the scraper/fetch tier).
- **Strategy-derived bets (deepen the seams)** — turn the NullProvider seams into real partner
  integrations (embedded finance, AISP bank feed), validated per-country SAF-T profiles + e-invoice/
  ERP export, expense-report depth (see `docs/STRATEGY.md` §7 / `docs/STRATEGY.md#backlog` §C).
- **Multi-tenancy phase 2** — the per-table `scope_clause()` wiring behind the `multitenant` switch.
- Test coverage for `invoice_control.py`/`ingest.py`/`build_master.py`/`history.py`; validated
  Postgres cutover (`docs/MANUAL.md#scaling-the-fleet-fuel-vat-refund-system`); `process_lock` fencing + per-job extract deadline (scale-gated).
