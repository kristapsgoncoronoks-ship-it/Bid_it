# ADR-0026 — One validation engine, one FX convention, one currency registry, one dimension registry

**Status:** Accepted — validation engine and FX convention implemented (WO-7, WO-8);
the currency-registry and dimension-registry unifications are **accepted but not yet
implemented** (board C1.5, C1.6, C1.7) — recorded here so the decision is not
re-litigated when that work is scheduled. Extends ADR-0010.

## Context

Three correctness defects were inherited, and all three were *duplication* — the
failure mode the "modules independent with clear interfaces" principle exists to
prevent:

1. **Two validators.** `services/validation.py` ran 14 deterministic checks —
   advisory, org-toggled, tolerances 0.01/0.02 — while `routes/invoice_review.py::
   _reconcile` ran an always-on, zero-tolerance, **blocking** gate at
   `POST /invoices/{id}/submit`. They disagreed, and the blocking one lived in a
   controller (business logic in a route — a defect by engineering-rules §3).
2. **Two FX conventions.** ECB rates are units-per-1-EUR and the invoice path
   divided; the expense path **multiplied**, and `fx_source` was unvalidated free
   text. Visible consequences: AP aging summed outstanding across currencies;
   reimbursement/payment-run EUR fallbacks labelled raw foreign totals `EUR` — which
   the SEPA file then emitted as `Ccy="EUR"`.
3. **Two currency registries and two dimension registries.** The tenant currency
   catalogue (`/currencies`) and the FX currency list (`/fx/currencies`) can
   disagree; the Explore pivot's dimension list does not include the five
   cost-allocation dimensions that the fixed by-dimension report reads.

## Selected approach

1. **One validation engine** (implemented, WO-7). All rules live in
   `services/validation.py` as a single registry (`RULES`); every rule carries an
   explicit **`block | advise`** policy and its own tolerance, and no rule is
   implemented twice. Blocking rules (policy `block`, tolerance exactly 0) are the
   AP submit gate — fail-closed, because an approved invoice locks. Advisory rules
   (policy `advise`, looser tolerances) are the opt-in, default-off findings that
   inform capture review and never gate — fail-open. The route layer only calls the
   service and shapes the response; `_reconcile` no longer exists in a route module.
2. **One FX convention** (implemented, WO-8; normative text lives in **ADR-0010**):
   ECB rates are units-per-1-EUR, converting to EUR **divides**, through the single
   entry point `services/fx.to_eur` shared by the invoice and expense paths.
   `fx_source` is a closed, CHECK-constrained enum `{eur, stated, ecb, unknown}`;
   `unknown` yields `NULL`, never a guessed number. A payout aggregate either uses a
   recorded EUR conversion or refuses, naming the line — a bank file never labels a
   foreign amount `EUR`. A daily scheduled job refreshes the ECB cache.
3. **One currency registry** (accepted, not yet implemented — C1.5): the tenant
   catalogue and the FX list must resolve through one registry so they cannot
   disagree about what a currency is or whether it is active.
4. **One dimension registry** (accepted, not yet implemented — C1.6, with C1.7
   extending single-currency discipline to every analytics surface): Explore and
   the fixed reports must read the same dimension registry (`core/dimensions` is
   the intended home), and `analytics.summary()`/benchmark/budget must stop
   hard-coding `"EUR"` — following the pattern the AR reports already get right
   (one currency per report, or a recorded conversion).

## Alternatives considered

- **Keep the blocking gate separate "because it's stricter"** — rejected: two
  engines that disagree is the defect, not a defence in depth; strictness is now a
  per-rule *policy*, not a second implementation.
- **Tolerate both FX conventions with a per-path flag** — rejected: a convention
  with two readings is not a convention; money invariant §4.15 admits one.
- **Leave registries duplicated until a bug forces the issue** — rejected as a
  *decision*; deferred as *work* (C1.5–C1.7) with the direction fixed here.

## Why appropriate

Every promise the product sells ("audit-ready", "the server recomputes every
total") dies when two engines disagree about the same invoice. Single-sourcing the
rules makes strictness a reviewable data point (`block | advise`) rather than an
accident of which code path ran, and the FX unification turns a silent
wrong-currency bug class into a refusal with a named cause.

## Risks

- The accepted-not-yet-implemented items (C1.5–C1.7) can silently regress further
  while open — mitigations: this ADR fixes the direction; the backlog entries carry
  the acceptance criteria; new code touching currencies/dimensions must not add a
  third registry.
- Zero-tolerance blocking rules can frustrate capture of genuinely sloppy supplier
  PDFs — mitigation: the advisory family and capture review exist precisely to fix
  the data *before* submit.

## Revisit when

C1.5/C1.6/C1.7 are implemented (fold their outcome in here and mark them done); a
rule needs a third policy beyond `block | advise` (e.g. `warn-once`); or a non-EUR
reporting pivot is required (ADR-0010's revisit trigger).
