# The platform — seven delegated works

The Fleet Fuel & VAT Refund System is an **accounting platform** for an agency that recovers
cross-border EU VAT (Directive 2008/9/EC) for five Baltic transport entities. The codebase
has a *technical* shape (the six blocks in `README.md` / `ARCHITECTURE.md`); this document
is the *product* shape — **seven delegated works**, each a single responsibility owned by a
module (and mostly its own database), sitting on a shared platform floor.

Each work is deliberately separable: one owner module, its own `connect()`, its own DB. That
is what lets a work be delegated to a background worker today and scaled to its own service
later (see `SCALING.md`).

---

## 1 · Data processing
**Turn raw supplier files into validated, reconciled transactions.**
- Owns it: `ingest.py` (xlsx/csv/xml/api), `extract.py` (PDF/ZIP→draft, parser registry),
  `waiting_room.py` (durable intake queue + background worker), then the engine
  `consolidate.py → validate.py → build_master.py → history.py`.
- Stores in: `fuel_history.db`; raw uploads archived in the data lake on arrival.
- Maturity: **high**. Gap: test coverage on `ingest`/`build_master`/`history`; finish the
  stored-master money sweep (`money.f2`).

## 2 · Invoice analytics & report export
**Turn transactions into intelligence and Excel deliverables.**
- Owns it: `pricing_intelligence.py` (competitiveness, self-sourced benchmark, overpay),
  `anomaly.py`, `contract_audit.py` (recoverable € per contract breach), `reports.py` and
  the master / history / summary / fees / pricing-grid exports. Plus `ai_review.py` — the
  **advisory** AI layer over already-extracted data (validation flags + a short analytics
  note), default-OFF, sends derived data only (never the document), never mutates or gates
  a figure; deterministic hard-checks stay in `validate.py`. See `docs/AI_REVIEW.md`.
- Maturity: **high**. Gap: detection dead-ends at read-only tables — no "act on it"
  (overcharge → recovery packet → supplier credit).

## 3 · Digital document storage
**Store originals immutably, verifiably, backend-agnostic.**
- Owns it: `document_vault.py` (local / SharePoint / FTPS, one logical tree), `data_lake.py`
  (extraction artifacts), `doc_storage.py`. SHA-256 dedup; `vat_refund.verify_documents()`
  re-hashes the live store against `invoice_documents.sha256`.
- Maturity: **high**. In progress: searching the vault to *attach* an existing file to a
  claim invoice (resolve a doc-missing block from the claim screen).

## 4 · Light CRM (API-plugin scalable)
**Manage the client + onboarding data that drives VAT eligibility.**
- Owns it: `customer_master.py` — entities, per-country activation, the adjustable checklist
  rules, document-template generation, fee terms, document validity/expiry.
- Stores in: `customers.db`.
- The **extensibility seam** (how it scales without multi-tenant surgery): the `extract.py`
  parser registry, the `portal_scraper.py` no-code adapter pattern, and the `/api/*` JSON
  surface. Generalising these into a documented, versioned **plugin/API contract** is the
  highest-leverage "make it a platform" move.
- Maturity: **medium**. Gap: the documented API-plugin contract.

## 5 · VAT processing
**Assemble and file 2008/9/EC refund claims.**
- Owns it: `vat_refund.py` (claim lifecycle 1A→5, claim workbook), `vat_config.py`.
- Stores in: the **isolated** `vat_claims.db` (kept apart so the monthly rebuild can't touch
  legal/financial records).
- Conventions: a claim is built from **registered invoices** — one row per
  (invoice, product code), never an `ALL:` aggregate, unresolved → `UNMATCHED`; the lock
  gate / readiness / checklist / workbook all refuse synthetic lines. Goods codes follow
  Art. 9 / Reg. 79/2012 (see `VAT_REFUND_RULES.md`).
- Maturity: **high**. In progress: rows stay editable (no freeze) with an accidental-edit
  guard.

## 6 · VAT control
**Guarantee every claim is complete, documented, one-invoice-one-submission.**
- Owns it: `invoice_control.py` (receipt control = cadence × activity, reconciliation
  triage, orphan check) + the submission gates in `vat_refund.py` (checklist, doc-presence,
  invoice locks, hard period-end) and `verify_documents`.
- Maturity: **medium-high**. Gap: wire the receipt-control required-set into the submission
  gate so a MISSING/orphan invoice is surfaced (and resolvable) at submit time.

## 7 · Invoicing for work
**Bill the agency's own service fee for the recovery.**
- Owns it: the fee engine in `vat_refund.py` (% of refunded VAT, per-declaration minimum
  floor, per-customer/country overrides, rate frozen at submission, charged at payout,
  settlement by where the refund lands) + `reports.fee_report_workbook`; the Recovery page.
- Maturity: **medium**. Gap: standalone fee-invoice numbering/ledger; AR aging beyond the
  current 120-day heuristic.

---

## Platform floor (under all seven)
`auth.py` (identity/roles), `audit.py` (immutable change history — *who* changed *what*),
`backup.py` (snapshots + SHA-256 integrity), `db.py`/`db_migrate.py`/`db_tuning.py`
(connections, versioned migrations, SQLite→Postgres path), `applog.py` + the admin error
log (errors self-control to the Admin panel), `tls.py`, `process_lock.py` (cross-process
leases + leader election). In accounting terms this is the **ledger-integrity, security and
infrastructure** layer that makes every one of the seven works auditable and trustworthy.

## Why this framing matters
- **The boundaries already exist** — one owner module + (mostly) one database per work — so
  each can be delegated to a worker and, with the Postgres/node-role work in `SCALING.md`,
  scaled independently.
- **#4's API plugin is the strategic seam** — the parser-registry / portal-adapter / JSON-API
  patterns are the lowest-risk way to turn a closed tool into a platform others integrate
  with, without multi-tenant surgery.
