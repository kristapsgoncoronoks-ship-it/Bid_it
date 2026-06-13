# Data architecture — duplication & under-used data

Audit of the system's databases for the owner's two concerns: (1) information **duplicated**
across DBs/tables that could be a single source of truth or derived, and (2) data **captured
but under-used** in analytics/reports/decisions. Grounded in code (file:line) and in
data-architecture best practice (SSOT/MDM; normalization vs deliberate denormalization;
derive-vs-materialize). Accounts for the decoupling already done (engine owns the product
DBs; app reads read-only via `dataproduct`; benchmark split to `benchmark.db`).

## The decision rule (best-practice litmus test)
For any repeated value:
- **Reference, don't copy** — must equal the current master (customer/supplier name, IBAN,
  VAT id) → store once in the master DB, reference by key. A cross-DB editable copy has no
  FK/trigger across SQLite files, so it drifts silently.
- **Freeze deliberately** — a fact about a moment (fee rate / FX / VAT at submission;
  SCD-Type-2 snapshot) → store on the transaction/claim, immutable. ✅ correct.
- **Derive & rebuild** — reporting/benchmark/cache value (gross, totals, aggregates) →
  derive on read, or materialize from the source of truth and keep it rebuildable.

## Part 1 — Duplication
| # | Fact | Where (file:line) | Verdict | Fix |
|---|---|---|---|---|
| **1** | Frozen claim `vat_eur`/`vat_local` OVERWRITTEN by `build_workbook` | frozen `vat_refund.py:479`; clobbered `:1005-1010` (no status guard) | 🔴 **BUG** — a workbook export after submission replaces the locked-claim_set base with an all-period recompute (undoes the F-C fee-base fix); they differ exactly when invoices are locked to other claims | gate the UPDATE to draft/un-submitted streams (skip `status in LOCKING`) |
| 2 | `supplier_invoices.gross_total` copied from the statement line | `invoice_control.py:255,258` | 🟠 accidental — insert-once, never re-synced → drifts if the statement is corrected | reference the statement line / re-sync on re-register |
| 3 | `gross`/`gross_local` stored = `net+vat` | `invoice_control.py:255`, `history.py:64` | 🟡 derivable (views already derive) | drop+derive, or add CHECK |
| 4 | Applied FX rate **not stored**, while 3 rate sources can disagree (`month_config.FX`=`1/4.27`, `ecb_fx`, `supplier_fx`) | `consolidate.py:102`; `ecb_rates.py:63`; `supplier_fx.py:26` | 🟠 SSOT gap — a claim's EUR figure isn't traceable to a named, dated rate | store the applied rate (or its `ecb_fx` key) per line/period; converge `month_config.FX` → `ecb_fx` |
| 5 | Receipt-control `status`/`expected` re-stored | `invoice_control.py:78` (`run_control` recomputes `:90-104`) | 🟡 derivable; only `waived`/`note` are real data | persist only the overrides; derive status |
| 6-8 | Frozen fee rate (`vat_refund.py:461-495`) · period-stamped pickle (`consolidate.py`/`history.py:48`) · multi-store doc index (vault/intake/lake/import_log = distinct lifecycle artifacts on a shared SHA-256) | — | ✅ **intentional & correct** (not redundancy) | keep |

## Part 2 — Under-used data
The `transactions` grain has 18 fields but analytics use ~7. Highest-value untapped assets:

| Data | Where (file:line) | Status | Leverage |
|---|---|---|---|
| **VAT lifecycle dates** (`approved_date` never read; only "unpaid N days" nudge) | stamped `vat_refund.py:453`; read `:1136-1149` | barely used | 🥇 **claim cycle-time + payout forecasting** — median submitted→paid by country, aging, expected-cash forecast |
| **`paid_amount` vs `vat_eur`** | `vat_refund.py:693` | not analyzed | **claim realization rate** — which jurisdictions haircut claims |
| **`transactions.time`** | `history.py:63` | **never read by any analytic** | time-of-day price/volume patterns; off-hours misuse anomaly flag (`anomaly.py`) |
| **`vehicle`** (no `card` at grain) | `anomaly.py:183` only | thin | per-vehicle €/L & consumption outliers |
| **Intake telemetry** (`started/finished_at`, `attempts`, `error`) | `waiting_room.py:81-85,404-413` | monitor panel only | **supplier processing-reliability scorecard** (durations, retry rate, failure reasons) |
| **Data-lake `meta.confidence`/`backend`** | `data_lake.py:80,95` | never mined | which suppliers' PDFs most need a deterministic parser (closes the `extract.py` registry loop) |
| **`wholesale_prices`** | `market_prices.py:122` | empty without external URL | one of three advertised margin baselines is silently inert → ship a default source or surface "not loaded" |
| `import_log` / `audit_log` | status panels / row browsing | un-aggregated | import-reliability trends; per-user rework hotspots |

## The unifying recommendation ("in increments")
Keep the engine-owned DBs as the single source of truth; **don't copy figures ad-hoc**:
- **Derive** the cheap copies (gross, totals) — eliminates drift.
- **Materialize** the expensive, read-heavy aggregates (overpay, benchmarks, fleet/cycle-time
  totals) into a **settled metrics table rebuilt at the monthly-close boundary** (or
  incrementally) — SQLite has no materialized views, so this is the right pattern and fits
  the engine-owns-the-product-DBs model; add a periodic recompute-and-compare drift check
  (like `verify_documents`). This both surfaces under-used data and removes per-request
  recompute.

## Prioritized actions
1. 🔴 **Fix #1** (the `vat_eur` clobber) — protects the fee fix; small.
2. **De-dup quick wins** — derive `gross`; persist only receipt-control overrides; add FX provenance.
3. **Leverage** — claim cycle-time + payout forecasting and realization rate (biggest VAT gap),
   then time-of-day/per-vehicle analytics and the supplier processing-reliability scorecard.

## Open product questions
- Is `build_workbook` meant to refresh *submitted* claims, or only drafts? (gates fix #1)
- Is `month_config.FX` authoritative, or should it derive from `ecb_fx`? (gates #4)
- Is payout forecasting in scope for the VAT surface?
- Should `card` be promoted to the transaction grain for per-card analytics?
