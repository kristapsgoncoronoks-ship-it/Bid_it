**WORK ORDER 87 — G4.7: the overpay / benchmark analyses (board G4.7; R52, R53's SECOND framing, R49, R51). Effort L. Priority P1. Milestone M5. Depends on: WO-82 (contract audit), WO-84 (`net_eur_eff` merge), WO-85 (the canonical query registry).**

### Objective and business value

`docs/plan/shared/specs/BA_fleet_fuel.md` §2.4 records a nuance the rebuild is
required to preserve and that this tree does not yet contain at all: **two
different "overpay" numbers coexist and will not reconcile** — `queries.q_savings`
(the *same-day, same-country, cheapest-rival* grain) and
`pricing_intelligence.internal_benchmark` (the *country × month,
best-of-your-own-suppliers* grain). Neither exists here. `grep -rn "q_savings"`
over the tree returns exactly two hits, both of them *promises*:
`app/services/transport/queries.py:89-93` (*"`q_savings` is the same-day overpay
grain (board G4.7) … They belong in this module when their boards land"*) and
`docs/transport/rules.md:50`. The same is true of R53's second framing:
`docs/transport/rules.md:48` records it as **STILL OPEN** in as many words —
*"the same-day overpay 'negotiation evidence, NOT a contractual claim-back'
sheet-level notice rides board G4.7"* — while `contract_audit.LEGAL_FRAMING`
("Money the supplier owes …") has been client-reachable since WO-83's claim
letter. And §2.5's **Expected rebate** analysis is a documented PARTIAL harvest:
`docs/plan/plan-a/wo/WO-84-net-eur-eff-merge.md:79` states *"the full 'Expected
rebate' analysis (a learned €/L typical rebate, flagged per line) is G4.7's; this
order harvests only the part R50 needs — the per-(supplier, country) existence
expectation"*, and `rebate.missing_source_warnings` is exactly that stub of it: a
`(supplier, country)` pair either has a recorded rebate document this period or
it does not. Nothing in the tree knows what a rebate for that pair usually
*looks like*, so nothing can flag the ONE line that quietly lost it.

Who pays. A haulier's fuel bill is its second-largest cost line and it is
negotiated, not posted: the numbers that move a supplier at the table are "on
2026-05-14 you charged us €0.0420/L more than the cheapest network we were
already using in LV, on 12,400 L" and "across LV in May, routing volume to the
supplier we were ALREADY buying from would have saved €3,180". That is money the
platform can find and a captive fuel-card scheme structurally will not
(`BA_fleet_fuel.md` line 71). It is also the analysis most likely to be *misused*:
an overpay figure is **not a debt**. WO-83 already ships a formal PDF letter with
a 30-day credit/refund demand for the contract-breach figure; if the negotiation
figure were presented in the same vocabulary the platform would be putting a
false assertion in front of a counterparty on our client's letterhead. R53 is
therefore the load-bearing constraint of this order, and this order makes the
separation **structural** rather than editorial.

### Scope

**In scope:**
- `app/services/transport/queries.py` — the registry gains the `product_group`
  dimension and **`price_comparison_transactions`**, the named cut the spec calls
  `q_savings`. No row-selection is written anywhere else (R51, WO-85's AST scan).
- `app/services/transport/savings.py` (**new**) — three read-only analyses, each
  strictly per its harvested definition:
  1. **Avoidable overpay (same-day)** — §2.5 row 1 verbatim.
  2. **Internal benchmark** — §2.5 row 2 verbatim (R52's second grain).
  3. **Expected rebate** — §2.5 row 8 verbatim, the learned €/L flagged per line.
- R53's **second framing** as a constant riding every result, and the structural
  vocabulary separation from the claim-back family, proven by test.
- `app/schemas/transport_savings.py` (**new**) — Decimal-typed, strings on the wire.
- `app/api/routes/transport/savings.py` (**new**) — three GETs on the existing
  `TRANSPORT_READ`; registered in the package aggregator.
- `docs/transport/rules.md` — the G4.7 row and R53's second consumer.
- `TODO.md` — the WO-87 row, the M5 cell, the suite line.

**Out of scope (named, with the board that owns them):**
- **Peer benchmark** (§2.5 row 3, R55 — the antitrust gate, `PEER_MIN_CONTRIBUTORS
  = 2`, intra-tenant cohort). Follow-up slice; needs a cross-ENTITY cohort policy
  decision this order must not take (board G4.7 continues).
- **Margin report / three baselines** (§2.5 row 4) — needs the `my_prices` /
  `wholesale_prices` benchmark tables of §3.H H5, which do not exist here.
- **Supplier reliability** (§2.5 row 5) — needs an append-only `advertised_prices`
  table (kept forever, so a past invoice can be re-checked against the price that
  applied on its date). No table, no analysis.
- **Anomalies** (§2.5 row 7, R54) — six rules, `ANOMALY_SIGMAS = 2.0`, modified-z
  3.5, volume floors. A whole order of its own; half-building two of six rules
  would be worse than none.
- **FX markup trend** (§2.5 row 9).
- Any UI. Every analytics board in this programme ships its SPA slice separately
  (WO-78/WO-80/WO-86); this order changes no pixel.
- Any change to `overcharge.py` / `overcharge_pack.py` / `contract_audit.py`.
  R53 is enforced by keeping them apart, not by editing them.

### Files to touch

| File | Change |
|---|---|
| `backend/app/services/transport/queries.py` | `product_group` dimension on `fuel_transactions`; new `price_comparison_transactions` |
| `backend/app/services/transport/savings.py` | **new** — the three analyses |
| `backend/app/schemas/transport_savings.py` | **new** — wire shapes |
| `backend/app/api/routes/transport/savings.py` | **new** — three GETs |
| `backend/app/api/routes/transport/__init__.py` | aggregate the new router |
| `backend/tests/transport/test_wo87_savings.py` | **new** — the analytic correctness |
| `backend/tests/transport/test_wo87_savings_routes.py` | **new** — the route matrix |
| `backend/tests/transport/test_wo87_r53_framing.py` | **new** — the framing separation |
| `docs/transport/rules.md` | G4.7 row; R53 second consumer |
| `TODO.md` | WO-87 row, M5 cell, suite line |

No table, no migration, no permission member, no dependency, no SPA page — so
`README.md`'s pinned scale numbers are untouched (`_py_module_count` globs
`app/services/*.py` and `app/api/routes/*.py` non-recursively; a transport
sub-package module does not move them, verified before writing this order).

### Implementation guidance

1. **The registry first.** `queries.fuel_transactions` gains
   `product_group: str | None = None`, filtering only when `is not None` (the
   module's stated convention). `price_comparison_transactions(org_id, *, period,
   country=None)` binds it to `"Diesel"`. It is the ONE place the diesel-only
   predicate is written.
2. **Both overpay grains use the same rows and must not be summed.** Each result
   carries a `grain` string (`GRAIN_SAME_DAY` / `GRAIN_COUNTRY_MONTH`) and the
   euro fields are deliberately *differently named* (`avoidable_eur` vs
   `benchmark_gap_eur`) so a consumer cannot add them by accident (R52).
3. **Currency basis, §4.14/§4.15 — the live gate of this order.** Every price is
   NET EUR/L, final = `net_eur_eff / qty` (§3.G G1 / R49). A same-day comparison
   partitions on `country`, so like is compared with like by construction. The
   EUR basis itself is *checked*, not assumed: a row whose `fx_source` is
   `"unknown"` (ADR-0010: *"no rate available → the EUR figure is NULL, never a
   guessed number"*), or a non-EUR row with no recorded conversion at all
   (`fx_source is NULL`), makes the analysis **refuse** with the existing
   `fx_rate_unavailable` code. It refuses rather than excluding, because a
   comparison set is not a list of independent objects: silently dropping one
   supplier's line changes the *cheapest rival* for every other supplier that
   day and can turn a real overpay into a zero or a zero into an overpay. Fails
   CLOSED.
4. **Rounding, §4.9.** Prices and deltas are exact Decimal quotients; the
   comparison is made UNROUNDED so an exact tie can never flip on presentation
   rounding. The euro is quantized ONCE (`money.q2`); display €/L are quantized
   to 4 dp and the delta is quantized from the exact difference, never from two
   already-rounded values (`contract_audit`'s discipline, unchanged).
5. **≥2 suppliers that day, positive deltas only** (verbatim). A day with one
   supplier yields NO finding and is counted in `days_without_a_rival` — never a
   false positive. "Rival" excludes the supplier itself, so the cheapest supplier
   of a day compares against the second-cheapest and drops out on the sign test.
6. **Expected rebate.** `applied = (net_eur − net_eur_eff) / qty`; the typical is
   the exact-Decimal **median** over the pair's rebate-bearing history (a mean
   would be dragged by an outlier and the zero lines would drag any average to
   zero, making the flag unfireable). A line is flagged when
   `abs(applied) < 0.005` (verbatim) and the pair HAS a learned typical. No
   minimum-sample constant is invented: `rebate.missing_source_warnings` already
   forms its expectation from a single prior period, and this order keeps that
   posture.
7. **Advisory (§4.19).** Nothing here gates, mutates or persists. `savings.py`
   issues no `db.add`, no attribute assignment, no flush, no audit event, and is
   proven read-only by a before/after column-by-column comparison of every
   transaction row.
8. **Route.** Thin controllers, `TRANSPORT_READ` at the router (the WO-79/WO-81
   reservation), three GETs, no write verb of any kind.

### Documented interpretations (stated, never silently assumed)

- **Both overpay grains are restricted to `product_group == "Diesel"`.** §2.5
  row 1 says "diesel only" verbatim; row 2 states its grain as country × month and
  is silent on product. Comparing a supplier's blended €/L across Diesel + AdBlue
  + Toll against another supplier's differently-mixed blend measures *product
  mix*, not price, and would manufacture a gap out of mix alone — a wrong money
  figure, the exact class of error this family exists to prevent. The narrowing is
  recorded here (the `contract_audit` most-specific-wins / `rebate` pro-rata
  precedent).
- **A supplier's price for a (country, day) — and for a (country, month) — is
  volume-weighted**, `Σ net_eur_eff / Σ qty`. §2.5's formula is `litres × (this
  supplier's eff €/L − …)`, which requires ONE €/L per supplier per grain cell;
  the volume-weighted mean is the only aggregate that makes `litres ×` reconcile
  with the underlying lines.
- **`expected_rebate_eur = typical €/L × litres`** is an arithmetic product of two
  harvested figures, reported as an advisory magnitude. It is not a claim, it is
  named so it can never be mistaken for one, and it never reaches
  `overcharge.detected_eur`.
- **The same-day analysis takes no `supplier` filter.** Filtering the rows would
  change the comparison set and therefore the cheapest rival — the figure would
  silently become a different figure. `country` is offered because groups are
  keyed on country and narrowing it cannot change any group's contents.

### Invariants this order must preserve

- **§4.9 / §4.14 / §4.15** — Decimal throughout; one currency basis (EUR),
  established per row rather than assumed; refuse-never-guess on a missing rate.
- **§4.19** — advisory: gates nothing, mutates nothing, feeds no claim-back euro.
- **§4.20 / §4.1 / §4.4** — additive; every cut org-scoped through the registry;
  a cross-tenant read returns zero rows, never another tenant's.
- **R51** — no forked row-selection; `price_comparison_transactions` is registered.
- **R52** — two grains, distinctly labelled, never summed.
- **R53** — the two framings are structurally separate (see below).
- **R49 / §3.G G1** — NET EUR/L, final, stated on every new report surface.

### Database / migration impact

**None.** No table, no column, no migration, no RLS policy, no tenancy-parity row.

### Testing requirements

`backend/tests/transport/test_wo87_savings.py`
- `test_wo87_same_day_overpay_is_litres_times_the_gap_to_the_cheapest_rival` —
  hand-computed Decimals, cent-exact.
- `test_wo87_an_exact_tie_is_not_an_overpay` — equal €/L ⇒ zero findings.
- `test_wo87_a_day_with_no_rival_yields_no_finding` — one supplier that day.
- `test_wo87_the_cheapest_supplier_of_the_day_is_never_flagged`.
- `test_wo87_the_comparison_never_crosses_a_country` (§4.14 scoping).
- `test_wo87_a_line_with_no_established_eur_basis_refuses` +
  `..._a_recorded_conversion_is_compared_in_eur` (§4.15, both sides).
- `test_wo87_internal_benchmark_is_country_month_best_of_your_own_suppliers`.
- `test_wo87_the_two_grains_do_not_reconcile_and_say_so`.
- `test_wo87_expected_rebate_learns_the_typical_eur_per_litre_from_history` +
  `..._flags_only_the_line_that_lost_it` + `..._a_pair_with_no_history_is_silent`.
- `test_wo87_zero_litre_lines_are_skipped_and_counted`.
- `test_wo87_the_analyses_write_nothing` (row-by-row before/after).
- `test_wo87_a_second_tenants_identical_rows_are_never_in_scope`.

`backend/tests/transport/test_wo87_r53_framing.py`
- `test_wo87_the_two_legal_framings_are_different_strings`.
- `test_wo87_the_overpay_surface_carries_no_claim_back_vocabulary` (field names of
  every result dataclass and every response schema).
- `test_wo87_the_overpay_analysis_is_not_reachable_from_the_claim_letter_path`
  (AST import scan, both directions).
- `test_wo87_the_savings_route_module_exposes_no_write_verb`.

`backend/tests/transport/test_wo87_savings_routes.py`
- granted / denied role pair; `TRANSPORT_READ` vs a role that lacks it.
- module-disabled ⇒ 403 `module_not_enabled`.
- cross-tenant: an org bound to A sees zero of B's identical rows.
- `invalid_period` 422; Decimal-as-string on every money field.

### Acceptance criteria

- [ ] `GET /api/v1/transport/savings/same-day?period=2026-05` returns
      `avoidable_eur` matching a hand-computed Decimal to the cent, with
      `grain == "same-day, same-country cheapest rival"`.
- [ ] Two suppliers at an identical €/L on the same day in the same country
      produce **zero** findings (not a €0.00 finding).
- [ ] A day with one supplier produces zero findings and increments
      `days_without_a_rival`.
- [ ] A PLN line with `fx_source` NULL makes all three analyses return 422
      `fx_rate_unavailable`; the same line with `fx_source="ecb"` is compared.
- [ ] `savings.LEGAL_FRAMING == "Negotiation evidence, NOT a contractual
      claim-back"` and is rendered on all three responses.
- [ ] No field name in `app/schemas/transport_savings.py` or in any
      `savings.py` dataclass contains `recover`, `owed`, `owes`, `claim`,
      `demand`, `due` or `debt`.
- [ ] `app/services/transport/savings.py` imports no overcharge module and no
      overcharge module imports it (AST-asserted, both directions).
- [ ] `app/api/routes/transport/savings.py` declares no POST/PUT/PATCH/DELETE.
- [ ] `python -m pytest tests/transport/test_wo87_*.py -q` green; full suite at
      or above the 2073 baseline with every delta explained.

### Rollback strategy

Pure code revert — the order adds files and one additive registry parameter, and
persists nothing. There is no migration to downgrade and no data to correct. The
narrow mitigation short of a revert is removing the three `include_router` lines'
module from the aggregator, which removes the surface while leaving the service
importable.

### Documentation to update

`docs/transport/rules.md` (G4.7 + R53's second consumer + R52), `TODO.md`
(WO-87 row, M5 cell, suite line). No ADR is contradicted: ADR-0023's projection
rule (*"every figure derives from one canonical query registry"*) is honoured by
routing the new predicate through `queries.py`.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/transport/test_wo87_savings.py \
                 tests/transport/test_wo87_savings_routes.py \
                 tests/transport/test_wo87_r53_framing.py -q
python -m pytest tests/transport/test_wo85_canonical_queries.py -q   # the registry still holds
python -m pytest -q                                                  # full baseline
python - <<'PY'
# DEMONSTRATION: the two framings cannot be flattened.
from app.services.transport import contract_audit, savings
assert contract_audit.LEGAL_FRAMING != savings.LEGAL_FRAMING
assert "owes" in contract_audit.LEGAL_FRAMING
assert "NOT a contractual claim-back" in savings.LEGAL_FRAMING
print(contract_audit.LEGAL_FRAMING); print(savings.LEGAL_FRAMING)
PY
python scripts/../../scripts/pii_scan.py --tree
```
