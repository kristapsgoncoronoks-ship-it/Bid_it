# WORK ORDER 85 — G4.1/R51: the canonical query registry for transport

**WORK ORDER 85 — the canonical query registry (board G4.1). Effort M (3–5d). Priority P0. Milestone M5. Depends on: WO-50 (`fuel_transactions`), WO-52 (`build_claim_lines`), WO-54 (`freeze`), WO-74 (`claim_pack`), WO-81 (`recovery`), WO-82 (`contract_audit`), WO-84 (`rebate`).**

---

## 0. Current-state recon (done BEFORE any design — "who already forks a canonical query?")

R38's "never a forked query" clause has been honoured **by hand** since WO-81. This order asks
whether hand-discipline actually held, and the answer is: **mostly, but not entirely — one shape
is genuinely re-derived byte-for-byte in two modules, and the org-scoping predicate over the two
money-bearing transport tables is re-typed at 17 separate sites.**

### 0.1 Every `select()` over the two money-bearing transport tables, as of `e3f491d`

`fuel_transactions` is the source of every €/L, every claim line and every north-star euro;
`vat_claim_lines` is the claim's own money grain. Seventeen call sites build a row-selection
predicate over them:

| # | Site | Predicate (verbatim order) | Cut it takes |
|---|---|---|---|
| 1 | `claim_lines.py:171` (`build_claim_lines`) | `org_id`, `entity_id`, `country`, `period.in_(months)` | the claim's scope |
| 2 | `checklist.py:237` (`_unresolved_suppliers`) | `org_id`, `entity_id`, `country`, `period.in_(months)` | **the same claim scope — byte-identical** |
| 3 | `contract_audit.py:681` (`audit`) | `org_id`, `period`, `[supplier]` + `order_by` | a period, optionally one supplier |
| 4 | `receipt_control.py:315` (`run_control`) | `org_id`, `period` | a period |
| 5 | `receipt_control.py:522` (`orphan_transactions`) | `org_id`, `period` | a period |
| 6 | `rebate.py:647` (`missing_source_warnings`) | `org_id`, `period`, `[supplier]`, projected to `(supplier, country)` | a period |
| 7 | `rebate.py:535` (`merge_period`) | `org_id`, `supplier`, `country`, `period` | one rebate group |
| 8 | `rebate.py:556` (`merge_period` phase 2) | `org_id`, `id.in_(ids)` | the planned rows |
| 9 | `tie_out.py:397` (`check_period`) | `org_id`, `entity_id`, `supplier`, `period`, `currency`, projected to 5 columns | one expectation's rows |
| 10 | `fuel.py:186` (`list_fuel_transactions`) | via `_filtered`: `org_id`, `entity_id`, `period.in_(months)`, `[supplier]`, `[country.upper()]` | one page |
| 11 | `fuel.py:184` (the count) | the SAME `_filtered` list | the page's total |
| 12 | `fuel_ingest.py:103` (`ingest_transaction`) | `org_id`, `entity_id`, `supplier`, `period`, `line_seq` | the WO-50 natural key |
| 13 | `capture_checks.py:269` (`_duplicate_findings`) | `org_id`, `invoice_ref.is_not(None)`, `upper(replace(invoice_ref,' ',''))` `.in_(refs)` | the cross-entity duplicate scan |
| 14 | `claim_lines.py:123` (`list_claim_lines`) | `org_id`, `claim_id` + `order_by` | every line of a claim |
| 15 | `claim_pack.py:230` (`_load_pack`) | `org_id`, `claim_id`, `frozen_at.is_not(None)` + `order_by` | the FROZEN lines |
| 16 | `freeze.py:108` (`_unfrozen_lines`) | `org_id`, `claim_id`, `frozen_at.is_(None)` | the UNFROZEN lines |
| 17 | `claim_gates.py:97` (`unfrozen_synthetic_refs`) | `org_id`, `claim_id`, `frozen_at.is_(None)`, projected to `(invoice_ref, vat_id)` | **the same unfrozen cut** |
| 18 | `document_gate.py:70` (`_resolved_invoice_ids`) | `org_id`, `claim_id`, `frozen_at.is_(None)`, `invoice_id.is_not(None)`, distinct | the unfrozen cut, resolved only |

### 0.2 What is actually forked today

**One genuine fork, and it is in the claim-building path.** Sites 1 and 2 are the same four
predicates in the same order over the same table, written out twice:

```python
# claim_lines.py:171 — what a claim's lines are BUILT from
select(FuelTransaction).where(
    FuelTransaction.org_id == org_id,
    FuelTransaction.entity_id == claim.entity_id,
    FuelTransaction.country == claim.refund_country,
    FuelTransaction.period.in_(months),
)
# checklist.py:237 — what the checklist REPORTS as blocking that same claim
select(FuelTransaction).where(
    FuelTransaction.org_id == org_id,
    FuelTransaction.entity_id == claim.entity_id,
    FuelTransaction.country == claim.refund_country,
    FuelTransaction.period.in_(months),
)
```

These two must agree or the product lies: `checklist._unresolved_suppliers` tells an operator
which suppliers are blocking a claim, and `build_claim_lines` decides which transactions become
claimable euros. If one drifts — a `station` filter, a `product_group` exclusion, a
`waived`-supplier pre-filter pushed into SQL — the checklist would report a clean claim whose
lines silently omit a supplier, or chase a supplier whose transactions are not in the claim at
all. Nothing in the tree stops that today.

**Three cuts of the same predicate, written three times.** Sites 16/17/18 are all "the claim's
currently-unfrozen lines" — the set `submit_claim` is about to freeze. `claim_gates.py:97`'s own
docstring already names the hazard: *"Mirrors `document_gate.missing_document_invoice_ids`'s
shape: one org-scoped query, shared by the blocking gate below and any future read-only preview
— never two independent line-scans that could drift."* It mirrors it **by hand**. The R3 lock
gate, the R10 document gate and the G2.5 freeze must scan exactly the same rows; three separate
`frozen_at.is_(None)` predicates is three chances for them not to.

**Fourteen re-typings of `org_id ==`.** Every one is correct today. Each is also an
independent opportunity to omit it — the failure mode master-context §4.1 calls a release
blocker and §4.4 makes invisible (a missing `org_id` filter returns another tenant's rows with
a 200, not a 403).

### 0.3 What is NOT forked (verified, so this order does not "fix" it)

- **Money arithmetic.** `contract_audit.audit` is the only €/L derivation in the tree
  (`contract_audit.py:659`: *"no other module derives €/L"*, verified — `grep -n "/ *qty\|/ Decimal(txn.qty)"` finds it only there). `recovery.recovery_dashboard` computes no figure of its own (WO-81's whole design). `rebate.missing_source_warnings` is called by `contract_audit`, never re-implemented.
- **The claim listing.** `claim.list_claims` is already the ONE listing query; WO-81 added its `year` keyword *additively* rather than writing a second one.
- **The period mapping.** `claim_lines.period_months` is the single quarter/year expansion;
  `fuel.resolve_period_months` delegates to it rather than re-expressing it.
- **The predicates.** `claim_gates.is_synthetic` (R3) and `invoice_match.resolve_invoice_ref`
  (R16) are each single-sourced with structural tests already proving no rival definition.

So the gap is precisely the **row-selection layer** — which rows a figure is computed over — and
nothing else. That is what this order registers.

### 0.4 The claim HEADER lookup — a repetition this order deliberately does not touch

`select(VatRefundClaim).where(id == claim_id, org_id == org_id)` appears in seven modules
(`claim.py:91`, `claim_lines.py:98`, `claim_pack.py:209`, `lock.py:92`, `status.py:97`,
`waiver.py:44`, `checklist.py:123`), each wrapped in a local `_get_claim` raising
`NotFoundError("Claim not found", code="claim_not_found")`. It is a repetition, but it is not a
**query fork**: it selects one row by primary key and no money figure is computed over it.
Folding it in would triple this order's blast radius for zero correctness gain. Recorded as the
named next slice, not silently ignored.

---

## Objective and business value

R51 is the one harvested rule with no implementing module: *"One canonical query layer. Every
report, export, dashboard and materialized metric derives from it; nothing forks the math."*
Its verification line is unusually specific — *"Rename a canonical function ⇒ every consumer
breaks; no duplicate implementation exists"* — and ADR-0023 already records the exposure in its
own Risks section: *"The projection rule is discipline until the canonical query registry is
complete — drift is possible in the interim."* §0.2 shows the discipline has already slipped
once, in the claim-building path, where a drift would be worth real money.

Who stops losing money: the client, silently. A forked row-selection does not raise, does not
fail a test and does not look wrong on screen — it just returns a different set of rows, and
every euro downstream is quietly computed over the wrong denominator. `checklist` and
`build_claim_lines` disagreeing by one supplier means either a claim filed short (unrecovered
VAT the client forfeits at the 30-Sep time-bar) or a supplier chased for an invoice that was
never in scope. This order converts "we were careful" into "the tree cannot express it", which
is the only form of that guarantee that survives the next thirty work orders.

---

## Scope

**In scope:**
- A new `backend/app/services/transport/queries.py` — the registry: six named, pure, org-scoped
  `Select` builders over `fuel_transactions` and `vat_claim_lines`. No I/O, no session, no money
  arithmetic.
- Migrating all 18 call sites in §0.1 onto it, behaviour-identical.
- `backend/tests/transport/test_wo85_canonical_queries.py` — the **structural anti-forking
  proof** (an AST scan over `app/services/transport/*.py` with its own seeded-violation
  self-test), the registry unit tests, org-scoping, and per-consumer equivalence with
  hand-computed `Decimal` expectations.
- `docs/architecture/adr/0023-...`'s Risks line + `docs/transport/rules.md` (a G4.1 row, and
  R38's registry consumer) + `TODO.md`.

**Out of scope:**
- **The materialised-metric half of R51** — *"materialized metrics have a drift check that
  recomputes through the same code path, and an un-materialized period still renders via a live
  fallback."* This codebase materialises **no** transport metric: there is no `settled_metrics`
  equivalent and no rollup table (verified against the live tree — the 17 `app/models/transport/*`
  tables are `vat_checklist_rules`, `vat_supplier_contract_terms`, `vat_customer_lifecycles`,
  `vat_country_activations`, `fuel_extraction_baselines`, `fuel_transactions`,
  `vat_claimed_invoices`, `vat_note_invoice_overrides`, `vat_off_invoice_rebates`,
  `vat_overcharge_claims`, `vat_supplier_cadences`, `vat_receipt_controls`,
  `vat_receipt_waivers`, `supplier_vat_registrations`, `fuel_tieout_expectations`,
  `vat_refund_claims`, `vat_claim_lines` — every one of them a source of record, not one of them
  a materialised aggregate). A drift check over nothing would be invented
  functionality (§10). Recorded as a PARTIAL HARVEST with its trigger, exactly as WO-60 recorded
  R45's deferred checklist items.
- `queries.q_savings` / `queries.q_ledger` — the two queries the spec names by name. Both belong
  to boards this order does not own (G4.7 overpay/benchmark, and the export hub). The registry
  is where they will land; building them here would be inventing their grain.
- The claim-header lookup of §0.4 (needs its own slice).
- Any change to a €/L, a bucket, a gate or a threshold. This order moves **where a predicate is
  written**, never what it selects.

---

## Files to touch

| File | Change |
|---|---|
| `backend/app/services/transport/queries.py` | **new** — the registry |
| `backend/app/services/transport/claim_lines.py` | sites 1, 14 → registry |
| `backend/app/services/transport/checklist.py` | site 2 → registry (closes the fork) |
| `backend/app/services/transport/contract_audit.py` | site 3 → registry + consumer-side `order_by` |
| `backend/app/services/transport/receipt_control.py` | sites 4, 5 → registry |
| `backend/app/services/transport/rebate.py` | sites 6, 7, 8 → registry |
| `backend/app/services/transport/tie_out.py` | site 9 → registry + `with_only_columns` |
| `backend/app/services/transport/fuel.py` | sites 10, 11 → registry; `_filtered` deleted |
| `backend/app/services/transport/fuel_ingest.py` | site 12 → registry |
| `backend/app/services/transport/capture_checks.py` | site 13 → registry + `NORMALIZED_INVOICE_REF` |
| `backend/app/services/transport/claim_pack.py` | site 15 → registry |
| `backend/app/services/transport/freeze.py` | site 16 → registry |
| `backend/app/services/transport/claim_gates.py` | site 17 → registry |
| `backend/app/services/transport/document_gate.py` | site 18 → registry |
| `backend/tests/transport/test_wo85_canonical_queries.py` | **new** — the proof |
| `docs/architecture/adr/0023-platform-evolution-and-transport-seam.md` | the Risks line, for the transport half |
| `docs/transport/rules.md`, `TODO.md`, `README.md` | boards + the collected-test figure |

---

## Implementation guidance

1. **Characterisation first.** The 2053-test suite already characterises all 18 sites through
   real service calls (`test_g2_4_claim_lines`, `test_g2_10_checklist`, `test_wo82_contract_audit`,
   `test_g3_3_tie_out`, `test_wo79_fuel_routes`, `test_g2_12_claim_pack`, `test_g2_5_freeze`,
   `test_r3_lock_gate`, `test_g2_6_document_gate`, `test_wo84_rebate_merge`, `test_g3_5_*`,
   `test_g3_4_capture_checks`). **Not one of them may be edited.** That is the equivalence net;
   the new file only adds to it.

2. **The registry is a predicate builder, not a service.** Pure functions returning
   `sqlalchemy.Select`. No `AsyncSession` parameter, no `await`, no `q2`, no module-entitlement
   check (the calling service already gates, ADR-P3 rule 3). `org_id` is the first positional
   parameter of every function — a builder that could omit it must not exist.

3. **The convention, stated once so a future consumer can follow it without asking:**
   the registry owns **which rows** (the WHERE clause); the consumer owns **how they are read**
   — projection (`.with_only_columns(...)`), ordering, grouping, pagination. Ordering is
   presentation, not math: `contract_audit`'s `order_by(supplier, txn_date, line_seq)` and
   `fuel`'s `(period, supplier, txn_date, line_seq)` stay at their call sites, where their
   docstrings already explain them.

4. **`None` means "every"; falsy-but-not-None is normalised at the call site.** Three existing
   sites use `if supplier:` (`contract_audit`, `fuel`) and one uses `if supplier is not None:`
   (`rebate`). A single registry convention must not silently change either, so the registry
   filters on `is not None` and the `if supplier:` sites pass `supplier or None`. That maps
   falsy→None exactly and preserves both behaviours. Same for `country`.

5. **Normalisation stays where it is.** `fuel._filtered` upper-cases `country`; the registry
   does not, and `fuel.list_fuel_transactions` passes `country.upper() if country else None`.
   A registry that normalised would change what `claim_scope_transactions` matches for any
   caller whose value was not already upper-cased.

6. **`period` and `months` are mutually exclusive** (`period == p` vs `period.in_(months)` —
   the two shapes the tree actually uses). Passing both is a programming error and raises
   `ValueError`, the same posture `fuel_card_parser.select` takes for an unroutable file.

7. **Migration order — smallest blast radius first, push each green:** (a) the registry + its
   unit tests; (b) the claim-line cuts (14–18, which close the freeze/gate/pack triple); (c) the
   claim-scope fork (1, 2 — the one real defect); (d) the period cuts (3–9); (e) the remaining
   fuel sites (10–13); (f) the structural test; (g) docs.

8. **If a migration would change a figure, stop and report it.** The falsy-`supplier` case in
   step 4 is exactly such a case, which is why it is normalised at the call site rather than
   unified.

---

## Invariants this order must preserve

- **§4.1 / §4.4 tenancy** — every registry builder emits `org_id = :org_id` unconditionally, and
  a test compiles each one and asserts the term is present. Migration removes 14 hand-typed
  org filters and replaces them with one that cannot be forgotten. The existing
  `tests/test_tenancy_parity.py` probes over `fuel_transactions` and `vat_claim_lines` are
  unchanged and must stay green.
- **§4.9 / §4.10 money** — the registry contains no arithmetic at all (asserted structurally: no
  `Decimal`, no `q2`, no `+`/`/` over a money column). Every euro keeps being computed by the
  service that already computes it.
- **§4.14 no cross-currency sums** — `tie_out.check_period` keys its predicate on
  `expectation.currency`, and `contract_audit` reports `currency="EUR"` over EUR columns. The
  registry carries `currency` as a first-class filter dimension precisely so that cut cannot be
  dropped; a test proves a two-currency period still tie-outs per currency.
- **§4.19 advisory** — `checklist`, `receipt_control`, `capture_checks` and `rebate`'s warnings
  stay advisory; nothing in this order adds or removes a gate.
- **§4.20 additive** — no signature of an existing public service function changes.
- **§10 nothing invented** — the module name `queries` is the spec's own
  (`queries.q_savings`, `queries.q_ledger`, `BA_fleet_fuel.md` §2.4/N10). The function names are
  descriptive per master-context §6 rather than `q_`-prefixed; recorded as an interpretation.

---

## Database / migration impact

**None.** No table, no column, no revision. 82 tables and 86 revisions are unchanged, so
`README.md`'s pinned counts are untouched by the code half of this order.

---

## Testing requirements

`backend/tests/transport/test_wo85_canonical_queries.py`:

- `test_wo85_no_transport_service_builds_a_rival_canonical_query` — the structural proof. AST-walks
  every module in `app/services/transport/` except `queries.py`, and asserts (a) no `select(...)`
  call references `FuelTransaction` or `VatRefundClaimLine`, (b) no `<Model>.org_id` attribute is
  referenced. Failure message names the module and line.
- `test_wo85_the_scanner_detects_a_seeded_violation` — the self-test (template rule 6): the same
  scanner run over a synthetic source string containing a forked `select(FuelTransaction)` must
  report it. A scan test that cannot fail proves nothing.
- `test_wo85_every_registry_query_has_a_real_consumer` — R51's own acceptance line ("rename a
  canonical function ⇒ every consumer breaks"): every public builder in `queries.py` is called by
  at least one module under `app/services/transport/`. No dead registry entries.
- `test_wo85_every_registry_query_is_org_scoped` — each builder compiled; `org_id = ` present in
  the SQL and the bound parameter equals the org passed.
- `test_wo85_registry_predicates_match_the_pre_migration_predicate_sets` — for each of the 18
  sites, the registry's WHERE criteria (as compiled strings, order-insensitive) equal the
  pre-migration predicate set recorded in §0.1.
- `test_wo85_period_and_months_together_is_a_programming_error` — `ValueError`.
- **Per-consumer equivalence, hand-computed:** `build_claim_lines` and
  `checklist.submission_checklist` over an overlapping fixture (right entity/country/period and
  three near-miss rows: wrong entity, wrong country, adjacent period) return exactly the same
  supplier set and exactly the hand-computed `Decimal` VAT total; `contract_audit.audit` returns
  the same `recover_eur` to the cent; `tie_out.check_period` ties out per currency over a
  two-currency period (§4.14); `fuel.list_fuel_transactions` returns `total` equal to the page
  set's size over the same filter; `freeze`/`claim_gates`/`document_gate` scan the identical
  unfrozen set (one fixture, one frozen line + one unfrozen synthetic + one unfrozen resolved).
- **Cross-tenant:** tenant B holds identical-looking rows (same supplier, same period, same
  amounts); every migrated consumer returns zero of them.

---

## Acceptance criteria (verifiable checklist)

- [ ] `backend/app/services/transport/queries.py` exists and exports exactly six public builders.
- [ ] `grep -n "select(" backend/app/services/transport/*.py | grep -E "FuelTransaction|VatRefundClaimLine"` returns **only** `queries.py` lines.
- [ ] `grep -rn "FuelTransaction.org_id\|VatRefundClaimLine.org_id" backend/app/services/transport/` returns **only** `queries.py` lines.
- [ ] `test_wo85_no_transport_service_builds_a_rival_canonical_query` is green, and
      `test_wo85_the_scanner_detects_a_seeded_violation` proves it can fail.
- [ ] Every pre-existing test file passes **unmodified** — `git diff --stat` shows zero changed
      lines under `backend/tests/` other than the new file.
- [ ] Full backend suite: 2053 passed / 10 skipped → 2053 + N passed / 10 skipped, zero
      regressions.
- [ ] `ruff check app tests && ruff format --check app tests && mypy app` clean.
- [ ] `docs/transport/rules.md` carries a G4.1 row naming `queries.py` and its structural test.

---

## Rollback strategy

Pure code revert — no migration, no data effect, no one-way action. The registry is additive:
reverting the consumer edits alone restores the previous predicates verbatim while leaving
`queries.py` in place (harmless, unused). The narrow mitigation for a single bad migration is to
revert that one call site; the other 17 are independent.

---

## Documentation to update

- `docs/transport/rules.md` — a G4.1 row (module, test, source), and R38's row gains the registry
  as the layer its "never a forked query" clause now rests on.
- `docs/architecture/adr/0023-platform-evolution-and-transport-seam.md` — the Risks line
  *"discipline until the canonical query registry is complete"* is now true only for the
  non-transport projections; the transport half is enforced.
- `TODO.md` — the WO-85 row, the M5 cell, the suite line.
- `README.md` — the collected-test figure only (no pinned count moves).

---

## Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1
python -m pytest tests/transport/test_wo85_canonical_queries.py -q
python -m pytest -q                                   # full baseline: 2053 -> 2053+N

# DEMONSTRATION 1 — the fork is gone (the old path cannot be written any more)
grep -rn "select(FuelTransaction\|select(VatRefundClaimLine" app/services/transport/ \
  | grep -v "queries.py"            # must print nothing

# DEMONSTRATION 2 — the guarantee can actually fail: seed a fork and watch CI catch it
python - <<'PY'
import pathlib
p = pathlib.Path("app/services/transport/checklist.py"); s = p.read_text()
p.write_text(s.replace("queries.claim_scope_transactions(", "select(FuelTransaction).where(", 1))
PY
python -m pytest tests/transport/test_wo85_canonical_queries.py -q   # must FAIL, naming checklist.py
git checkout app/services/transport/checklist.py
```
