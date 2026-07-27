# ADR-0010 — Decimal money + ECB FX provenance + VAT engine

**Status:** Accepted

## Context
Wrong money is the fastest way to lose trust and incur liability. We handle multi-currency, multi-scheme EU VAT across legal entities.

## Selected approach
- **Decimal end-to-end**, `ROUND_HALF_UP`, via `core/money` (`f2/fsum/q2`); storage columns `Numeric(14,2)`; **never `round()` on currency**.
- **FX** converts to EUR at the **ECB reference rate for the invoice's date**, recording **provenance** (rate + source + date). EUR is the pivot; **mixed-currency aggregation is forbidden** — reports are single-currency.
- **ONE conversion convention (WO-8):** an ECB rate is *units of the foreign currency per 1 EUR*; converting **to EUR divides** (`services/fx.to_eur` is the single entry point — the invoice path and the expense path share it). Rate selection is the most recent rate **on or before** the transaction date (a Sunday transaction uses Friday's rate). `fx_source` is a **closed enum** `{eur, stated, ecb, unknown}` (`models/fx.FxSource`, CHECK-constrained on `invoices` and `expense_items`); **`unknown` yields `total_eur = NULL`, never a guessed number**. A payout aggregate (payment run, reimbursement batch, SEPA pain.001) either uses a recorded EUR conversion or **refuses, naming the line and the missing rate** — a bank file never labels a foreign amount `EUR`. The ECB cache is refreshed by a daily scheduled job (one global job per day; graceful on failure — cached rates keep serving).
- **VAT engine** computes per scheme (standard, reverse-charge, intra-EU, exempt), produces the rate breakdown, and attaches the required legal note; net EUR, VAT-exclusive basis stated on every surface.
- **FI-10 generalised past AR/AP (C1.7/WO-24):** `issued_reports.py` (AR) and `ap_aging.py` (AP, WO-8) established the "no mixed-currency total" rule first; `analytics.py`, `benchmark.py` and `budget.py` now follow it too, in the two sanctioned SHAPES:
  - **Pick-one-currency-and-filter** (`analytics.py`, `benchmark.py`, mirroring `issued_reports._pick_currency`/`ap_aging.summarize`): resolve the tenant's most-used currency (or an explicit `?currency=` filter), scope every underlying query to it, and surface every OTHER currency present via `available_currencies` — never fold a second currency into the same total. Row-shaped endpoints (`spend_over_time`, `top_vendors`, `by_category`, `by_status`, `supplier_benchmarks`) are wrapped `{currency, available_currencies, rows}` rather than a bare array, so the currency a chart or table is scoped to is always named on the wire.
  - **Convert-to-EUR-or-exclude** (`budget.py`, mirroring `payment_run.eur_of`/`reimbursement.eur_of`'s fail-closed rule): household budgeting wants one canonical EUR total across every category, not a per-currency split, so the module converts every invoice via its recorded `total_eur` — but an invoice with `fx_source == "unknown"` (no rate ever resolved) is now EXCLUDED from every actual/trend total, never guessed at a 1:1 parity the way the old `coalesce(total_eur, total)` fallback did. `BudgetOverview.excluded_unconverted` discloses the gap instead of hiding it.
  Both shapes satisfy §4.14/§4.15; which one applies depends on whether the report's job is "compare currencies side by side" (pick-and-filter) or "one number across categories" (convert-or-exclude) — see `docs/plan/plan-a/wo/WO-24-C17.md` for the reasoning.

## Alternatives considered
- **Float money** — rejected outright; rounding drift is unacceptable for finance.
- **Integer minor-units** — valid and fast, but Decimal is equally exact here and more readable across schemes/rates; consistency with the existing code wins.
- **Convert everything to one currency and sum** — silently wrong; forbidden.

## Why appropriate
Exactness + auditability are non-negotiable for financial records; provenance makes every figure defensible in an audit; single-currency reporting avoids a whole class of silent errors. Matches statutory VAT correctness the product sells on.

## Risks
- New country/scheme edge cases → golden-file tests per scheme; sampled correctness audits; counsel review for new markets.
- Stale/missing ECB rate for a date → cached rates + explicit `fx_source` (eur/stated/ecb/unknown); never guess silently.

## Revisit when
A new market introduces tax rules the engine can't express, or a customer needs a non-EUR reporting pivot — extend the engine + provenance model, never relax the invariants.
