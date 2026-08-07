# WO-82 — supplier overcharge detection + claim-back (G4.5 / R41)

> The second M5 row, and the order that CLOSES WO-81's deviation 1. WO-81
> deliberately OMITTED the `overcharges` euro from the cash-recovery dashboard
> rather than emit a zero, because a zero labelled "overcharges" reads as *"we
> found no supplier breaches"* — a different and false statement. This order
> builds the service that figure comes from, then wires it in.
>
> **Correction recorded during implementation:** the self-verification block's
> "detection writes nothing" check originally scanned the WHOLE
> `contract_audit` module for `db.add`. That is wrong — the term CRUD lives in
> the same module and legitimately writes (audited old→new, §4.16). The check
> below now targets the detection functions themselves, which is exactly what
> the shipped test `test_wo82_detection_holds_no_writable_intent` asserts,
> alongside a behavioural before/after comparison of every fuel-transaction
> column.

**WORK ORDER 82 — supplier overcharge detection (`app/services/transport/
contract_audit.py`) + the per-(supplier × period) claim-back lifecycle
(`app/services/transport/overcharge.py`) + its route
(`app/api/routes/transport/overcharges.py`), and the completion of
`recovery.py`'s documented `overcharges` hole. Effort L 6–12d. Priority P1.
Milestone M5 (board G4.5, rule R41). Depends on: WO-50 (`fuel_transactions` —
the validated line ledger detection reads), WO-79 (the fuel read/accessor
pattern), WO-81 (the dashboard this completes), WO-72/WO-73 (the transport-local
registry-table + audited-lifecycle patterns this mirrors).**

### Objective and business value

The gap, with verified evidence. `grep -rn "contract_audit\|overcharge"
backend/app` returns ONLY docstring prose — `app/services/transport/recovery.py`
lines 116-121 name the hole in their own DEVIATIONS section (*"it comes from
`contract_audit`/`overcharge.py` — board **G4.5 / R41**, which does not exist in
this codebase"*), `app/api/routes/transport/__init__.py` names `overcharges.py`
as a future includer, and `TODO.md`'s M5 row lists G4.5 as *"the missing
`overcharges` euro this dashboard deliberately omits"*. `docs/plan/plan-a/
ARCH_plan.md` line 977 is the board row. Today the platform can tell an operator
what VAT is recoverable from a tax authority and nothing at all about money the
SUPPLIER owes back under an agreed commercial term — even though every input
(litres, the as-invoiced net, the effective net after rebates) has been in
`fuel_transactions` since WO-50.

Who stops losing money: the FINANCE_MANAGER who signs the fuel-card contracts.
`BA_fleet_fuel.md` §2.4 frames this surface as *"What does the supplier owe us
for breaching the contract?"* and §2.4's legal-framing table is emphatic that
this analysis — alone among the price analyses — is **"Money the supplier owes"**
backed by *"a claim letter with a 30-day demand"*, as opposed to the same-day
overpay figure which is explicitly *"negotiation evidence, NOT a contractual
claim-back"* (R53). A contracted €0.20/L rebate silently not applied to 40,000 L
in a month is €8,000 of hard, legally-owed cash that nobody notices, because it
is invisible in every per-invoice view: the invoice is arithmetically correct,
it is only wrong against the contract. Detection is worth nothing without a
worklist, which is why R41 pairs it with a lifecycle and a booked-cash total —
`recovered_total` is the figure that says how much of the found money actually
came back.

### Scope

**In scope:**
- `backend/app/models/transport/contract_term.py` (**new**) —
  `VatSupplierContractTerm`, the agreed commercial term a supplier can breach
  (`BA_fleet_fuel.md` §4.4's `supplier_discounts` grain, verbatim).
- `backend/app/models/transport/overcharge.py` (**new**) — `VatOverchargeClaim`,
  the per-(supplier × period) claim-back row with the harvested six-state
  lifecycle.
- One alembic migration creating BOTH tables with their RLS policies in the
  SAME migration (master-context §4.2), registered in
  `app/core/tenant.py::TENANT_MODELS`.
- `backend/app/services/transport/contract_audit.py` (**new**) — term CRUD
  (audited old→new) + `audit()`: read-only breach detection over
  `fuel_transactions`, NET EUR/L basis.
- `backend/app/services/transport/overcharge.py` (**new**) — `open_claim`,
  `advance_claim`, `list_claims`, `get_claim`, `recovered_total`.
- `backend/app/services/transport/recovery.py` — the `overcharges_eur` figure,
  obtained by CALLING `overcharge.recovered_total` (R38's "never a forked
  query"), plus the DEVIATIONS docstring updated to record the hole as closed.
- `backend/app/schemas/transport_overcharge.py` (**new**) — Decimal-typed wire
  models; `backend/app/schemas/transport_recovery.py` — the new field.
- `backend/app/api/routes/transport/overcharges.py` (**new**) — thin controllers;
  `backend/app/api/routes/transport/__init__.py` includes it.
- `backend/app/services/audit.py` — three new action constants.
- `backend/tests/transport/test_wo82_contract_audit.py`,
  `test_wo82_overcharge_lifecycle.py`, `test_wo82_overcharge_routes.py` (**new**).
- `backend/tests/test_tenancy_parity.py` — a probe for both new tables.
- Boards: `TODO.md`, `docs/transport/rules.md` (R41 row + R38's completed
  consumer), `README.md` scale line.

**Out of scope (named, with the board id that owns them):**
- **The two send-ready ARTIFACTS.** R41 also requires *"an Excel evidence packet
  and a formal PDF claim letter (with a credit/refund demand and a deadline)
  built from the SAME line source"*, and R53 requires the framing text on each.
  Both are a FOLLOW-UP SLICE of G4.5. This order ships the detection + lifecycle
  + the single `_lines_for`-shaped line source they must both render from, and
  records the artifact slice as the recommended next one. Building two document
  renderers here would double the order and delay the euro the dashboard is
  missing.
- The other C5 analyses (`BA_fleet_fuel.md` §2.5) — same-day avoidable overpay,
  internal/peer benchmark, margin report, supplier reliability
  (`OVERCHARGE_TOL_EUR_PER_L`), anomalies, expected rebate, FX markup trend.
  Board **G4.7**. In particular the reliability/`advertised_prices` analysis is a
  DIFFERENT question ("does the supplier invoice what it ADVERTISED?") with a
  different tolerance and a different legal framing; it is not this order.
- `net_eur_eff`'s off-invoice rebate MERGE and its source guard (R50) — board
  **G4.2**. This order READS `net_eur_eff` as the ingestion tier already writes
  it (WO-64 recorded that Q8/Port One reconciliation as G4.2's own row).
- `/excise` (G4.6), `/value`, `/claim-status` (G4.4), the estimate funnel (G4.8).
- Any SPA screen. Service + route only, the WO-79/WO-81 shape.

### Files to touch

| File | Change |
|---|---|
| `backend/app/models/transport/contract_term.py` | **new** — `VatSupplierContractTerm` |
| `backend/app/models/transport/overcharge.py` | **new** — `VatOverchargeClaim` |
| `backend/alembic/versions/<rev>_overcharge_claimback.py` | **new** — both tables + RLS |
| `backend/app/core/tenant.py` | register both models in `TENANT_MODELS` |
| `backend/app/models/transport/__init__.py` | export both models |
| `backend/app/services/transport/contract_audit.py` | **new** — terms + detection |
| `backend/app/services/transport/overcharge.py` | **new** — the lifecycle |
| `backend/app/services/transport/recovery.py` | `overcharges_eur`; DEVIATIONS updated |
| `backend/app/services/audit.py` | 3 new action constants |
| `backend/app/schemas/transport_overcharge.py` | **new** — the wire models |
| `backend/app/schemas/transport_recovery.py` | `overcharges_eur` |
| `backend/app/api/routes/transport/overcharges.py` | **new** — thin controllers |
| `backend/app/api/routes/transport/__init__.py` | include the new router |
| `backend/app/api/routes/transport/recovery.py` | pass the new field through |
| `backend/tests/transport/test_wo82_contract_audit.py` | **new** |
| `backend/tests/transport/test_wo82_overcharge_lifecycle.py` | **new** |
| `backend/tests/transport/test_wo82_overcharge_routes.py` | **new** |
| `backend/tests/transport/test_wo81_recovery.py` | one added assertion for the new field |
| `backend/tests/test_tenancy_parity.py` | probe for both new tables |
| `docs/transport/rules.md` | R41 row; R38's consumer completed |
| `backend/tests/test_docs_truth.py` | the hard-coded table count, 79 → 81 (a truth-up) |
| `docs/DECISIONS-NEEDED.md` | §12 — abandoning a claim-back before it is sent (recorded only) |
| `TODO.md` / `README.md` | boards, counts |

### The harvested definitions — every rule, with its citation

Everything below is quoted from `docs/plan/shared/specs/BA_fleet_fuel.md`. No
Fleet Fuel code, constant table, fixture or datum is read or reproduced.

**1. What an agreed term IS** — §4.4's supplier-master schema fragment, verbatim:

> `supplier_discounts (supplier, country, station_like, product_group,
> expected_discount_eur_l, max_net_eur_l, active)   ← contract terms`

**2. What a BREACH is** — §2.5's "Contract audit" row, verbatim:

> *"Two term types only: **`expected_discount_eur_l`** (rebate that should be
> applied, €/L) and **`max_net_eur_l`** (contracted NET price ceiling, €/L).
> Flags: `"short discount"` (`applied < expected − tol`) and `"over ceiling"`
> (`eff_l > max + tol`). `recover_eur = gap × litres`, dropped if ≤ 0. **No
> volume-tier / stepped-rebate / annual-bonus / card-fee modelling.**"*
> Constant: **`TOLERANCE = 0.005 €/L`** (env `AUDIT_TOLERANCE_EUR_L`).

**3. The comparison basis** — §3.G G1 and R49, verbatim, and it is exactly the
project's stated basis, verified rather than assumed:

> *"G1. Prices everywhere are NET EUR/L, FINAL — VAT excluded, rebates applied.
> This basis must be stated on any new report surface. → NET/effective price =
> `net_eur_eff / qty`."*
> R49: *"NET EUR/L, final (VAT excluded, rebates applied) is THE price basis,
> stated on every report surface. Effective price = `net_eur_eff / qty`. Both
> the as-invoiced (`eur_l_doc`) and effective (`eur_l_eff`) prices are exposed
> so the rebate value is visible."*

So: `eur_l_eff = net_eur_eff / qty`, `eur_l_doc = net_eur / qty`, and the
discount ACTUALLY APPLIED per litre — the `applied` of the "short discount"
flag — is `eur_l_doc − eur_l_eff`, i.e. `(net_eur − net_eur_eff) / qty`. That
identity is exactly §4.2's own two-tier discount model: an on-invoice discount
is *"already inside `net_eur`"*, an off-invoice rebate *"lands ONLY in
`net_eur_eff`"*, so their difference is the rebate layer and nothing else.

**4. The claim-back lifecycle** — §4.5's state table and R41, verbatim:

> §4.5: *"| Overcharge claim-back | `detected → packaged → claimed → recovered |
> rejected | written_off` |"*
> R41: *"a per-(supplier × period) lifecycle `detected → packaged → claimed →
> recovered | rejected | written_off` … and a `recovered_total` that feeds the
> north star. Read-only over the analytics."*

**5. What the "€ overcharges" north-star figure MEANS** — §2.4's `/overcharges`
row, verbatim: *"`recovered_total()` = the booked-cash north star."* So the
dashboard's `overcharges` euro is **booked cash actually recovered**, not the
detected exposure. Both are reported here, under distinct names, because they
answer different questions and R52's own discipline ("label them distinctly")
applies.

**6. The legal framing** — §2.4's framing table + R53: `contract_audit` /
`overcharge` = *"Money the supplier owes"* — a claim letter with a 30-day demand.
Carried as the framing constant this order defines and the artifact slice will
print.

### Documented interpretations (stated because the spec does not settle them)

1. **Term precedence when two terms match one line.** The natural key permits a
   wildcard term (`station_like = ""`) AND a station-specific one for the same
   (supplier, country, product_group). Applying both would double-count
   `recover_eur` — a wrong money figure. Rule: **most specific wins** — a
   non-empty pattern beats the wildcard; among non-empty patterns the LONGEST
   wins; ties broken lexicographically so the result is deterministic. Exactly
   one term is ever applied to a line.
2. **The tolerance is a module constant, not an env var.** §2.5 names
   `AUDIT_TOLERANCE_EUR_L`. A per-deployment env knob on a money threshold is a
   config-surface decision this codebase has not taken for any other harvested
   constant (the R25 tolerances, `DEADLINE_RISK_DAYS` and the Art. 17 minimums
   are all module constants), so the harvested VALUE ships and the env override
   does not. Recorded, not silently dropped.
3. **The claim-back period is `YYYY-MM`** — `FuelTransaction.period`, the grain
   detection runs at. R41 says "per-(supplier × period)" without naming which
   period vocabulary; the transaction's own accounting month is the only one the
   evidence lines share, and a quarter is expressible as three claims.
4. **`written_off` is reachable only from `claimed`.** §4.5 draws the chain
   linearly. No shortcut edge is invented (the WO-73 precedent: `inactive` is
   terminal because no re-onboarding edge was harvested).
5. **A `recovered` transition requires an amount in `(0, detected_eur]`.** Not a
   harvested rule — it is master-context §4 invariant 13 (no over-crediting)
   applied to this ledger: booking back more than was ever demanded would make
   `recovered_total` — a north-star figure — unbounded by the evidence.
6. **`open_claim` refuses when detection finds nothing.** A claim-back at €0 is
   a letter demanding nothing; `no_overcharge_detected` (422) is the fail-closed
   answer, and it keeps `recovered_total`'s denominator honest.

### Implementation guidance (execution order)

1. **Models + migration first**, both tables, RLS in the same migration,
   `TENANT_MODELS` registration, then `alembic heads` (single) + `alembic check`
   (no drift) + the clean-from-empty guard tests.
2. **`contract_audit.py`**: `set_term` / `remove_term` / `list_terms` (audited
   old→new, module-gated fail-CLOSED, `active` toggle) then `audit()`. Money:
   every division and product is `Decimal`; `eur_l` values are compared
   UNROUNDED against the tolerance (a boundary must not flip on a rounding step)
   and `recover_eur` is quantized ONCE at the end through `app.core.money.q2`,
   ROUND_HALF_UP. Basis stated in the module docstring, every function docstring
   and the route docstring: **NET EUR/L, final — VAT excluded, rebates applied.**
   `qty <= 0` lines are skipped (§4.2: a promo correction line can carry qty 0;
   dividing by it is undefined, and a zero-litre line cannot breach a €/L term).
3. **`overcharge.py`**: the six-state machine, `TRANSITIONS` as data, every real
   transition audited old→new IN THE SAME transaction as the mutation (§4.16),
   an invalid edge refused 409 `overcharge_transition_invalid` with NOTHING
   mutated (assert the row is byte-identical afterwards).
4. **`recovery.py`**: add `overcharges_eur` by CALLING `overcharge.
   recovered_total(db, org_id, year=year)`. Do NOT re-derive it (R38). Keep it
   OUT of the `recovered + awaiting + claimable` identity — that identity is
   about VAT and must stay exactly reconciled; the overcharge euro is a second,
   separate cash stream and the docstring says so.
5. **Route**: router-level `TRANSPORT_READ` (WO-79/WO-81's reservation for
   derived analytics), write verbs overriding to `VAT_WRITE` (see below).
6. **Tenancy parity**: convert both new tables from would-be EXEMPT rows into
   real HTTP probes with OVERLAPPING data (identical supplier codes, identical
   periods, identical amounts across the two orgs).

### Permission decision (justified, not invented)

**Reads: `TRANSPORT_READ`**, router-level. This is the derived-analytics slice
`app/api/routes/transport/fuel.py` explicitly reserved it for: *"`TRANSPORT_READ`
stays reserved for the derived analytics/excise slices (`recovery.py`/`excise.py`/
`overcharges.py`)"* — `overcharges.py` is named in that very sentence.

**Writes: `VAT_WRITE`**, per-route override, the `admin.py` `_WRITE` pattern.
`VAT_WRITE` is this codebase's ONE transport write permission and it already
governs every transport surface that is not a VAT-claim STATUS flip: cadences,
tie-out expectations, note overrides, checklist rules and the R44 customer
lifecycle (WO-77) — none of which is "VAT" either. `VAT_SUBMIT` is deliberately
NOT used: it is reserved for actions that acquire or release an invoice LOCK on
a VAT claim (`ISSUED_SEND`'s reasoning, `authz.py`'s own comment), and an
overcharge claim-back touches no claim, no lock and no VAT figure.
**No permission member is added** (§10). The role sets already align: every role
holding `VAT_WRITE` (OWNER, ADMINISTRATOR, FINANCE_MANAGER, ACCOUNTANT) also
holds `TRANSPORT_READ`, so no write route is unreachable behind its own reads,
and AUDITOR/READ_ONLY get the analytics without the worklist verbs.

### Invariants this order must preserve

- **§4.9 Decimal-only.** No float anywhere: `Decimal` division for €/L, `q2`
  once at the money boundary, `Numeric` storage. A `grep -n "float"` over the
  new modules returns nothing, asserted by a test.
- **§4.14 no cross-currency sums.** Detection compares and sums in **EUR only**:
  `net_eur`/`net_eur_eff` are EUR by construction (`fuel_ingest` refuses a line
  it cannot convert — `fx_rate_unavailable`, WO-69's two-phase guarantee), so a
  mixed-currency supplier month is natively summable in EUR and **no coercion of
  a foreign figure ever occurs**. The document-currency columns
  (`net_local`/`vat_local`/`gross_local`) are NEVER read — a structural test
  asserts their absence from the module — and each finding carries its line's
  document currency as PROVENANCE that is never totalled. The claim row stores
  EUR and the response states `currency: "EUR"` explicitly.
- **§4.10 the server recomputes.** `open_claim` snapshots the figure DETECTION
  produced; a client-supplied amount is accepted only for `recovered_eur` (the
  externally-observed cash), and it is bounded by the detected total.
- **§4.16 audit old→new in-transaction.** Every term write and every lifecycle
  transition emits an `audit.record` with `old_*`/`new_*` in `meta`, added to
  the same session the mutation is in; no-ops audit nothing (the
  `checklist.set_active` convention).
- **§4.19 advisory never blocks or mutates.** `contract_audit` is READ-ONLY over
  `fuel_transactions` — no `db.add`, no attribute assignment, no re-rating. §3.L
  is explicit that this family of analyses never gates a legal figure; nothing
  here touches a VAT claim, a line, a lock, a freeze or the close.
- **§4.20 additive.** Two new tables, four new modules, one new route module, one
  additive dashboard field. No existing behaviour changes; `recovery.py`'s six
  buckets and three VAT euros are byte-identical.
- **§4.1-4.4 tenancy.** Both tables org-scoped with a composite `(org_id, id)`
  unique for future FK targets, `TENANT_MODELS`-registered, FORCE RLS in the
  creating migration, opaque 404 on a cross-tenant claim id.
- **§10 nothing invented.** The two term types, the two flag strings, the
  tolerance, the six states and the `recovered_total` semantics are all quoted
  above. Every reading the spec does not settle is in the interpretations list.
- **§9 actual vocabulary.** The flags are the harvested strings `"short
  discount"` / `"over ceiling"`; the states are the harvested six.

### Database / migration impact

Two new TENANT tables, RLS in the SAME migration.

`vat_supplier_contract_terms` — `(org_id, supplier, country, station_like,
product_group)` UNIQUE; `expected_discount_eur_l` / `max_net_eur_l`
`Numeric(12,4)` nullable with a CHECK that at least one is present (a term that
asserts nothing is not a term); `active` boolean; `product_group` CHECKed
against the WO-50 `PRODUCT_GROUPS` set; composite `(org_id, id)` unique.

`vat_overcharge_claims` — `(org_id, supplier, period)` UNIQUE; `status` CHECKed
against the six harvested states; `detected_eur`/`recovered_eur`
`Numeric(14,2)`; `lines_count`; `note`; composite `(org_id, id)` unique.

Downgrade drops both tables and their policies — it loses claim-back history and
the configured terms, and that is stated in the migration docstring. Nothing
outside these two tables changes, so a downgrade cannot corrupt a claim.

### Testing requirements

`backend/tests/transport/test_wo82_contract_audit.py`
- `test_wo82_short_discount_is_detected_against_a_hand_computed_decimal`
- `test_wo82_over_ceiling_is_detected_against_a_hand_computed_decimal`
- `test_wo82_exact_boundary_applied_equals_expected_is_not_a_breach`
- `test_wo82_exact_boundary_eff_equals_max_is_not_a_breach`
- `test_wo82_inside_the_tolerance_is_not_a_breach_and_one_step_outside_is`
- `test_wo82_no_agreed_terms_means_no_finding`
- `test_wo82_an_inactive_term_produces_no_finding`
- `test_wo82_a_zero_quantity_line_is_skipped_not_divided_by`
- `test_wo82_the_most_specific_station_term_wins_and_never_double_counts`
- `test_wo82_recover_eur_is_dropped_when_not_positive`
- `test_wo82_detection_mutates_no_transaction` (row-by-row byte comparison)
- `test_wo82_mixed_currency_lines_total_in_eur_and_never_sum_a_local_amount`
- `test_wo82_the_module_reads_no_document_currency_column` (structural)
- `test_wo82_terms_are_org_scoped`

`backend/tests/transport/test_wo82_overcharge_lifecycle.py`
- one test per legal edge (`detected→packaged`, `packaged→claimed`,
  `claimed→recovered|rejected|written_off`)
- `test_wo82_an_illegal_transition_is_refused_with_nothing_mutated`
- `test_wo82_a_terminal_state_accepts_no_further_transition`
- `test_wo82_every_transition_writes_an_audit_row_old_to_new`
- `test_wo82_open_claim_is_idempotent_on_the_natural_key`
- `test_wo82_open_claim_refuses_when_detection_found_nothing`
- `test_wo82_recovering_more_than_detected_is_refused`
- `test_wo82_recovered_total_counts_only_recovered_claims`
- `test_wo82_recovered_total_is_org_scoped`
- `test_wo82_the_dashboard_overcharges_euro_is_the_recovered_total`

`backend/tests/transport/test_wo82_overcharge_routes.py`
- `test_wo82_accountant_is_granted_and_employee_is_denied` (both verbs)
- `test_wo82_an_auditor_can_read_but_not_advance` (TRANSPORT_READ without
  VAT_WRITE — the reason the two permissions differ)
- `test_wo82_module_disabled_refuses_403_module_not_enabled`
- `test_wo82_a_cross_tenant_claim_id_is_an_opaque_404`
- `test_wo82_amounts_cross_the_wire_as_decimal_strings`
- `test_wo82_the_list_is_org_scoped_with_overlapping_data`
- `test_wo82_an_invalid_period_is_refused_422`
- `test_wo82_an_unknown_status_is_refused_422`

`backend/tests/test_tenancy_parity.py` — a probe covering both new tables.

### Acceptance criteria (verifiable checklist)

- [ ] A 1,000 L diesel line at €1.30/L effective with an agreed
      `expected_discount_eur_l = 0.20` and an applied discount of €0.05/L yields
      exactly one `"short discount"` finding with `gap = 0.1500` and
      `recover_eur = "150.00"`.
- [ ] The same line with an applied discount of exactly €0.20/L yields ZERO
      findings; at €0.1950/L (exactly one tolerance short) still ZERO; at
      €0.1949/L one finding.
- [ ] A supplier with no configured term yields ZERO findings over the same
      transactions (never a false positive), and `lines_without_terms` reports
      the count.
- [ ] `POST /api/v1/transport/overcharges` on a (supplier, period) with no
      findings returns 422 `no_overcharge_detected` and creates no row.
- [ ] `POST /transport/overcharges/{id}/advance` with `to_status="claimed"` on a
      `detected` claim returns 409 `overcharge_transition_invalid`, and a fresh
      read shows `status == "detected"` with every column unchanged.
- [ ] Each legal transition writes an `AuditEvent` whose `meta` carries
      `old_status` and `new_status`.
- [ ] `GET /transport/recovery-dashboard?year=YYYY` returns `overcharges_eur`
      equal to the sum of `recovered_eur` over that year's `recovered`
      claim-backs, as a JSON string, and `recovered_eur + awaiting_eur +
      claimable_eur` still reconciles exactly as WO-81 asserts.
- [ ] An org whose transactions span EUR and PLN produces one EUR total equal to
      the hand-computed Decimal sum, and `grep -n "net_local\|vat_local\|
      gross_local"` over `contract_audit.py` returns nothing.
- [ ] `role_client` for EMPLOYEE gets 403 on both the read and the write; an
      AUDITOR gets 200 on the read and 403 on the advance.
- [ ] Tenant B's claim id passed to a tenant-A session returns 404, never 403.
- [ ] `alembic heads | wc -l` is 1; `alembic upgrade head && alembic check` is
      clean; `alembic downgrade -1 && alembic upgrade head` round-trips.
- [ ] `python -m pytest -q` is green at 1900 + the new tests, 10 skipped, zero
      pre-existing tests modified.

### Rollback strategy

Code revert plus one migration downgrade. The downgrade drops
`vat_supplier_contract_terms` and `vat_overcharge_claims` (and their policies) —
it LOSES the configured contract terms and the claim-back history, which is
stated in the migration docstring; nothing else is touched, so no VAT claim,
line, lock or audit chain can be affected. Narrower mitigation without a
downgrade: remove the `router.include_router(overcharges.router)` line and drop
the `overcharges_eur` field — the surface disappears while the tables and the
services stay intact and importable.

### Documentation to update

- `docs/transport/rules.md` — a new **R41** row; **R38**'s row updated to record
  that its deliberately-omitted `overcharges` deviation is now CLOSED.
- `TODO.md` — the WO-82 row, the M5 cell (G4.5 shipped, artifacts pending), the
  M3 cell (`overcharges.py` now has a backing service), the suite line.
- `README.md` — the scale line (counts verified, not assumed), and
  `backend/tests/test_docs_truth.py`'s hard-coded table count with it.
- `docs/DECISIONS-NEEDED.md` — §12, abandoning a claim-back before it is sent
  (recorded, not decided: the harvested chain reaches `written_off` only from
  `claimed`, and this order refuses to invent the missing edge).
- No ADR is contradicted; ADR-P3's rules 1/2/3/5 are followed as-is (a
  transport-local tenant table, org-scoped service-level entity resolution,
  entitlement inside the service, existing permissions only).

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic upgrade head && alembic check
python -m pytest tests/transport/test_wo82_contract_audit.py \
                 tests/transport/test_wo82_overcharge_lifecycle.py \
                 tests/transport/test_wo82_overcharge_routes.py -q
python -m pytest tests/test_tenancy_parity.py tests/test_authz_coverage.py \
                 tests/test_boundaries.py tests/test_rls.py -q
python -m pytest -q                                   # full baseline: 1900 -> 1900+N
python -c "
import inspect
import app.services.transport.contract_audit as ca
src = inspect.getsource(ca)
assert 'float' not in src, 'float in a money path'
for col in ('net_local', 'vat_local', 'gross_local'):
    assert col not in src, f'{col} read in a EUR-only analysis (§4.14)'
# The write check targets the DETECTION functions only: the term CRUD lives in
# the same module and legitimately writes (audited old->new, §4.16). The
# corresponding test is test_wo82_detection_holds_no_writable_intent.
for fn in (ca.audit, ca._term_for, ca._breach_for, ca._finding):
    fsrc = inspect.getsource(fn)
    assert 'db.add' not in fsrc and 'db.delete' not in fsrc and 'db.flush' not in fsrc
    assert 'audit_svc.record' not in fsrc
print('contract_audit.py is EUR-only, float-free, and its detection half writes nothing')
"
grep -n "overcharges" app/api/routes/transport/__init__.py
python ../scripts/pii_scan.py --tree
```
