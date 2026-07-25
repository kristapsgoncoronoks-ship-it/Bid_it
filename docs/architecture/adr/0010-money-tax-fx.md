# ADR-0010 — Decimal money + ECB FX provenance + VAT engine

**Status:** Accepted

## Context
Wrong money is the fastest way to lose trust and incur liability. We handle multi-currency, multi-scheme EU VAT across legal entities.

## Selected approach
- **Decimal end-to-end**, `ROUND_HALF_UP`, via `core/money` (`f2/fsum/q2`); storage columns `Numeric(14,2)`; **never `round()` on currency**.
- **FX** converts to EUR at the **ECB reference rate for the invoice's date**, recording **provenance** (rate + source + date). EUR is the pivot; **mixed-currency aggregation is forbidden** — reports are single-currency.
- **ONE conversion convention (WO-8):** an ECB rate is *units of the foreign currency per 1 EUR*; converting **to EUR divides** (`services/fx.to_eur` is the single entry point — the invoice path and the expense path share it). Rate selection is the most recent rate **on or before** the transaction date (a Sunday transaction uses Friday's rate). `fx_source` is a **closed enum** `{eur, stated, ecb, unknown}` (`models/fx.FxSource`, CHECK-constrained on `invoices` and `expense_items`); **`unknown` yields `total_eur = NULL`, never a guessed number**. A payout aggregate (payment run, reimbursement batch, SEPA pain.001) either uses a recorded EUR conversion or **refuses, naming the line and the missing rate** — a bank file never labels a foreign amount `EUR`. The ECB cache is refreshed by a daily scheduled job (one global job per day; graceful on failure — cached rates keep serving).
- **VAT engine** computes per scheme (standard, reverse-charge, intra-EU, exempt), produces the rate breakdown, and attaches the required legal note; net EUR, VAT-exclusive basis stated on every surface.

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
