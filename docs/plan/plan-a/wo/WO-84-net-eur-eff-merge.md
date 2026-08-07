# WORK ORDER 84 — G4.2/R50: the off-invoice rebate merge into `net_eur_eff` + the source guard

**WORK ORDER 84 — the off-invoice rebate merge into `net_eur_eff` and its source guard (board G4.2). Effort M (3–5d). Priority P0. Milestone M5. Depends on: WO-50 (`fuel_transactions` + `ingest_transaction`), WO-53 (`run_close`), WO-82 (`contract_audit`), WO-83 (the two artifacts).**

---

## 0. Current-state recon (done BEFORE any design — the answer to "is `net_eur_eff` just a copy of `net_eur`?")

**Yes. In production it is always an exact copy.** Verified, symbol by symbol:

| Question | Verified answer |
|---|---|
| Where is `net_eur_eff` declared? | `backend/app/models/transport/fuel_transaction.py:243` — `Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)`. The model comment already states the intent: *"Effective net EUR after ALL rebate layers, INCLUDING off-invoice ones … Defaults to net_eur when no off-invoice rebate applies."* |
| Which code paths WRITE it? | Exactly **one**: `backend/app/services/transport/fuel_ingest.py:134` — `net_eur_eff=q2(net_eur_eff if net_eur_eff is not None else net_eur)`. There is no `UPDATE` of the column anywhere in `app/`. |
| Does any production caller pass a non-`None` value? | **No.** The only caller of `ingest_transaction` in `app/` is `statement_ingest.ingest_statement` (`statement_ingest.py:325`) and its argument list does **not** include `net_eur_eff`. All seven `parsers/*` modules leave it at the default, four of them saying so in as many words (`q8.py:51`, `tfc.py:40`, `moeve.py:68`, `parsers/__init__.py:12`, each naming **G4.2** as the owner). The only callers that pass it are **tests**. |
| Consumers that already assume a merge happened? | **Yes — one, and it is the money-demand path.** `contract_audit._breach_for` (`contract_audit.py:544`) computes `eur_l_eff = net_eur_eff / qty` and then `actual = eur_l_doc − eur_l_eff` — documented in that module's own docstring as *"the rebate ACTUALLY applied, §4.2"*. Because `net_eur_eff == net_eur` for every production row today, **`actual` is identically `0.0000`**. |

### Why that matters, in euros

`contract_audit`'s first flag is `short discount`: `actual < expected − TOLERANCE`. With `actual ≡ 0`, **every** line governed by a term carrying `expected_discount_eur_l > 0.005` flags as a short discount, and `recover_eur = expected_eur_l × litres` — the **full** contracted rebate. That figure is not academic:

- `overcharge.open_claim` **freezes** it as `detected_eur`;
- `overcharge_pack.build_evidence_packet` / `build_claim_letter` (WO-83) **print** it in a formal letter that demands *"a credit note or refund within 30 days"*;
- `recovery.overcharges_eur` totals it into the cash-recovery dashboard's north star.

So today, for a supplier that **does** pay its rebate off-invoice (the harvested canonical case: Q8 invoices at list price, Port One issues a separate rebate invoice per country), this platform sends a letter demanding money the supplier has **already paid**. That is the defect this order closes, and it is why WO-84 had to follow WO-82/WO-83 rather than precede them: the artifacts exist, so the wrong number is now reachable by a client.

### The harvested definitions this order implements (cited, not paraphrased)

- **`BA_fleet_fuel.md` §4.2, row 16** — `net_eur_eff` = *"Effective net EUR after ALL rebate layers, including OFF-INVOICE ones. Defaults to `net_eur`."*
- **`BA_fleet_fuel.md` §4.2, "the two-tier discount model (a business rule, not a technicality)"**, verbatim:
  - *"**On-invoice** discounts (TFC hub discount −0.205/L, E100 station-colour tiers 0.08–0.23/L, MOEVE PRN off pump PVP, DKV 1.30 SEK/L + 5.63% service fee) are already inside `net_eur`."*
  - *"**Off-invoice** rebates land ONLY in `net_eur_eff`. **The canonical case is Q8/Port One:** Q8 invoices at **LIST price** per country; Port One issues a **SEPARATE rebate invoice per country**. The pair must always be reconciled. This is the entire reason the `net_eur_eff` column exists."*
  - *"**Hazard:** the Q8 rebate layer depends on `month_config.FILES["Q8"]` pointing at the *adjusted* workbook. There is no assertion that it does — swapping in the raw file **silently loses the rebate layer**, corrupting every price/benchmark/overpay figure."*
- **R50** (`BA_fleet_fuel.md` §7.5), verbatim: *"**`net_eur_eff` carries off-invoice rebate layers** (the canonical case: a supplier invoicing at list price with a separate rebate invoice per country). **Guard the input source** so a raw file cannot silently replace an adjusted one."* Acceptance: *"Feed the un-adjusted source ⇒ the pipeline **fails or warns loudly**, it does not silently produce list-price analytics."*
- **R49** (§7.5) / **§3.G G1** — the basis: *"NET EUR/L, final (VAT excluded, rebates applied) … Effective price = `net_eur_eff / qty`"*, with both `eur_l_doc` and `eur_l_eff` exposed *"so the rebate value is visible"*. **Already shipped** by WO-82 (`contract_audit.PRICE_BASIS`, both €/L columns on every `Breach`, printed on both WO-83 artifacts). This order does not redo it; it makes the `eur_l_eff` half **mean something**.
- **`BA_fleet_fuel.md` §2.5, "Expected rebate"**, verbatim — the harvested source of the guard's *expectation*: *"Learns the typical €/L rebate per (supplier, country) from all history; flags a line where it is **missing** (`abs(rebate) < 0.005`). Rationale: catches rebates that arrive on a **separate invoice we often don't see** (the Q8/Port One case)."*
- **`ARCH_plan.md` G4.2** — *"**Guard the input source**: feeding an unadjusted file must **fail or warn loudly** — today the Q8 rebate layer silently disappears if the raw workbook is swapped in, corrupting every price, benchmark and overpay figure."* *Acceptance: R50 verbatim.*

### What "the source guard" guards — read off the spec, not invented

The harvested hazard has two halves, and the guard has one answer for each:

1. **A rebate figure must never be inferred.** Fleet Fuel's rebate arrived pre-merged inside a workbook whose provenance nothing asserted. Here a rebate may enter `net_eur_eff` **only** from an identified, recorded rebate document — a row in `vat_off_invoice_rebates` carrying a non-empty `source_ref` **and** a non-empty `source_party` (§5.1 names the rebate issuer as a distinct party: *"Q8 / Kuwait Petroleum (+ **Port One** rebate partner)"*). Enforced **fail-closed** at the service (`rebate_source_required`) and again by a `CHECK` at the database. `merge_period` reads recorded rows and nothing else — it has no code path that can derive, estimate or carry-forward a rebate.
2. **A rebate layer must never silently disappear.** This is the literal harvested hazard. §2.5's "Expected rebate" gives the mechanism verbatim: the expectation is **learned from history**, per `(supplier, country)`. A `(supplier, country)` that has a recorded rebate source in **any earlier** period, has fuel activity in **this** period, and has **no** recorded source for this period is exactly "the raw file was swapped in". It **warns loudly** — on the merge result, and on `contract_audit.audit()`, which is the precise surface where "list-price analytics" would otherwise be produced silently.

Per master-context §4.19 the warning is **advisory** — it never blocks and never changes a euro. Per R50 it is *"fails **or** warns loudly"*: this order takes the warn branch at the analytics surface and the **fail** branch everywhere a wrong figure would be written (see "fail-closed refusals" below).

### Deviation recorded during recon (not caused by this order)

`TODO.md`'s **Test suite** line was last updated at WO-82 (`1971 passed, 10 skipped`); WO-83 shipped 36 further tests without extending it. The live tree collects **2017** tests at `03c9326`, and `README.md`'s scale line still says `1981 collected backend tests`. The collected-test figure is deliberately *not* asserted by `tests/test_docs_truth.py` (`_readme_tests` is unpacked and left unchecked — *"most volatile figure (grows every WO)"*), so CI was never red. This order re-pins both.

---

## Objective and business value

`net_eur_eff` has been a column with a docstring and no behaviour since WO-50: one writer, no caller, and a value provably equal to `net_eur` on every production row (`fuel_ingest.py:134`; `statement_ingest.py:325` omits the argument; all seven parsers say so explicitly). Meanwhile `contract_audit._breach_for` (`contract_audit.py:544`) already reads it as though a merge had happened, so its `applied = eur_l_doc − eur_l_eff` term is identically zero and its `short discount` flag over-claims by the entire contracted rebate. WO-83 turned that number into a signed PDF demanding payment within 30 days. The gap is therefore no longer latent.

Who stops losing money: the client, twice. First, the platform stops sending a supplier a demand for a rebate the supplier already paid — one such letter costs the client its credibility in the next contract negotiation, which is worth more than the claim. Second, every downstream price figure becomes true: the effective €/L a client is actually paying is the number the cash-recovery dashboard, the overpay comparison and the future benchmark all rank on, and a list-price effective figure inflates it by the whole rebate. R50's own acceptance sentence names the stake exactly — *"it does not silently produce list-price analytics."*

## Scope

**In scope:**
- **new** `backend/app/models/transport/off_invoice_rebate.py` — `VatOffInvoiceRebate`, the recorded rebate document (grain `(org, supplier, country, period, source_ref)`), org-scoped, composite `(org_id, id)` unique, `CHECK`s for the source guard.
- **new** `backend/alembic/versions/<rev>_off_invoice_rebates.py` — the table + `ENABLE`/`FORCE ROW LEVEL SECURITY` + policy **in the same migration**; single head preserved.
- `backend/app/core/tenant.py` — register the model in `TENANT_MODELS`; `backend/app/models/transport/__init__.py` — export it.
- **new** `backend/app/services/transport/rebate.py` — `record_rebate` (FX-resolving, audited, source-guarded), `list_rebates`, `merge_period` (the recompute), `missing_source_warnings` (the history-learned guard).
- `backend/app/services/transport/close.py` — the merge as a **close stage**, ahead of the claim-line rebuild.
- `backend/app/services/transport/contract_audit.py` — `ContractAuditResult` gains an **additive** `source_warnings` tuple; the arithmetic is untouched.
- **new** `backend/app/schemas/transport_rebate.py`; `backend/app/schemas/transport_overcharge.py` — pass `source_warnings` through.
- **new** `backend/app/api/routes/transport/rebates.py` (`POST` on `VAT_WRITE`, `GET` on `TRANSPORT_READ`); registered in `backend/app/api/routes/transport/__init__.py`.
- `backend/app/services/audit.py` — three action constants.
- `backend/tests/test_tenancy_parity.py` — `vat_off_invoice_rebates` as a **real HTTP probe**, in the same commit that creates the table.
- `README.md` — the scale line, in the same commit as the migration/model/service/route counts it pins.
- Boards **last**: `TODO.md`, `docs/transport/rules.md` (the **R50** row + the R49/R41 consumer updates).

**Out of scope:**
- **G4.1** — the canonical query registry. This order calls `contract_audit.audit()`; it forks no query and builds no registry.
- **G4.7** — the two overpay definitions, the peer benchmark, the anomaly bounds, §2.5's *other* analyses. In particular the full "Expected rebate" analysis (a learned **€/L** typical rebate, flagged per **line**) is G4.7's; this order harvests only the part R50 needs — the per-`(supplier, country)` *existence* expectation.
- **G4.6** `/excise`, **G4.4** `/claim-status`, **G4.8** the estimate funnel.
- Any SPA screen. Service + route only (the WO-79/WO-81/WO-82 shape).
- Changing how a **parser** produces figures. No parser is touched: an off-invoice rebate is by definition not on the statement the parser reads.
- On-invoice discount verification (already inside `net_eur`, §4.2) — that is `contract_audit`'s existing `max_net_eur_l` ceiling, shipped in WO-82.

## Files to touch

| File | Change |
|---|---|
| `backend/app/models/transport/off_invoice_rebate.py` | **new** — `VatOffInvoiceRebate` |
| `backend/app/models/transport/__init__.py` | export it |
| `backend/alembic/versions/<rev>_off_invoice_rebates.py` | **new** — table + FORCE RLS policy |
| `backend/app/core/tenant.py` | register in `TENANT_MODELS` |
| `backend/app/services/transport/rebate.py` | **new** — record / list / merge / guard |
| `backend/app/services/transport/close.py` | new merge stage + its summary in the close audit meta |
| `backend/app/services/transport/contract_audit.py` | additive `source_warnings` on the result |
| `backend/app/services/audit.py` | `TRANSPORT_REBATE_RECORD` / `_UPDATE` / `_MERGE` |
| `backend/app/schemas/transport_rebate.py` | **new** — `RebateIn` / `RebateOut` |
| `backend/app/schemas/transport_overcharge.py` | `source_warnings` on the audit response |
| `backend/app/api/routes/transport/rebates.py` | **new** — two thin controllers |
| `backend/app/api/routes/transport/__init__.py` | include the router |
| `backend/tests/transport/test_wo84_rebate_merge.py` | **new** — the arithmetic + guards |
| `backend/tests/transport/test_wo84_rebate_routes.py` | **new** — the HTTP surface |
| `backend/tests/test_tenancy_parity.py` | new real probe param |
| `backend/tests/test_docs_truth.py` | table count 81 → 82 |
| `README.md` | scale line |
| `TODO.md`, `docs/transport/rules.md` | boards, LAST |

## Implementation guidance

1. **Model + migration first.** `vat_off_invoice_rebates`: `supplier(60)`, `country(2)`, `period(7)`, `source_ref(120)`, `source_party(120)`, `rebate_date`, `currency(3)`, `amount_local Numeric(14,2)`, `amount_eur Numeric(14,2)`, the FX quadruple (`fx_rate`/`fx_ecb_rate` `Numeric(18,6)`, `fx_ecb_date`, `fx_source(16)`), `note` text. `UNIQUE(org_id, supplier, country, period, source_ref)` — a second rebate **document** for the same country/period is a legitimate second row; the same `source_ref` twice is the same document. `UNIQUE(org_id, id)`. `CHECK(source_ref <> '')`, `CHECK(source_party <> '')`, `CHECK(amount_local > 0)`, `CHECK(amount_eur > 0)`. RLS `ENABLE` + **`FORCE`** + `tenant_isolation` policy in the same migration, the `a7c2e9f14b58` pattern verbatim.
2. **`record_rebate` — the identified-source half of the guard, fail-CLOSED.** Module entitlement → `validate_period` → country shape → `source_ref.strip()`/`source_party.strip()` empty ⇒ `ValidationError(code="rebate_source_required")` **before any query**; `amount_local <= 0` ⇒ `rebate_amount_invalid`. Re-recording the same `(supplier, country, period, source_ref)` **updates** it (a corrected rebate document) and audits **old→new** per changed field (§4.16); a first sight inserts and audits the recorded figure.
3. **FX — §4.15, refuse rather than guess.** `currency == "EUR"` ⇒ identity, `fx_source="eur"`, no ECB lookup. Otherwise `fx.to_eur(db, amount_local, currency, rebate_date)` — the platform's one convention (ECB rates are units per 1 EUR; converting to EUR **divides**); `(None, None)` ⇒ `ValidationError(code="fx_rate_unavailable")`, **nothing written**, exactly `statement_ingest`'s branch and code. On success `fx_source="ecb"`, `fx_rate = fx_ecb_rate = r.rate`, `fx_ecb_date = r.as_of`. No `stated` branch: a rebate invoice in this model states an amount, never a rate; inventing one would be the guessed number §4.15 forbids.
4. **`merge_period` — the recompute, on the engine side.** For each `(supplier, country)` with recorded rebates in `period`: `rebate_total = Σ amount_eur` (all EUR — §4.14 is satisfied by construction, never a raw cross-currency sum). Load that `(supplier, country, period)`'s transactions ordered by `line_seq`.
   **Allocation (a DOCUMENTED INTERPRETATION — the spec gives the layer, not a formula): pro-rata by `qty` (litres).** Justified three ways and stated, never guessed: §5.1's rebate vocabulary is uniformly €/L (0.08–0.23/L, 0.205/L, 1.30 SEK/L — a fuel rebate is a per-litre instrument); it makes `applied = eur_l_doc − eur_l_eff` a **constant** €/L across the country's lines, which is the shape `contract_audit` compares against a €/L `expected_discount_eur_l`; and a value-weighted alternative would make `applied` vary with price and manufacture short-discount flags on the expensive lines. Lines with `qty == 0` (§4.2 row 9's promo corrections) receive **nothing** — they have no litres to rebate.
   **Rounding: the running-cumulative method, so the allocated cents sum EXACTLY to `rebate_total`.** Walking rows in `line_seq` order with a cumulative litre count, `cum_eur_i = q2(rebate_total × cum_qty_i / total_qty)` and `share_i = cum_eur_i − cum_eur_(i−1)`. Every figure `Decimal`, `q2` = ROUND_HALF_UP (§4.9). `net_eur_eff_new = q2(net_eur − share_i)`.
   **Always recompute from `net_eur`, never from the current `net_eur_eff`.** That single rule is what makes the merge idempotent: re-running writes nothing and audits nothing.
5. **Fail-CLOSED refusals in the merge, resolved before any write** (the `statement_ingest` two-phase guarantee — a refused merge leaves **zero** rows changed): `total_qty == 0` for a country carrying a rebate ⇒ `rebate_no_litres_to_allocate` (silently dropping a recorded rebate is exactly the disappearance R50 forbids); any `net_eur_eff_new <= 0` ⇒ `rebate_exceeds_net` (a negative effective net would corrupt every €/L that reads it); a rebate recorded for a `(supplier, country, period)` with **no** transactions ⇒ `rebate_has_no_transactions`.
6. **Audit old→new (§4.16).** One `TRANSPORT_REBATE_MERGE` event per **changed** transaction: `meta = {old_net_eur_eff, new_net_eur_eff, rebate_share_eur, source_refs}`. An unchanged row emits nothing — which is what makes the idempotency test assertable ("re-running the merge writes no new audit row").
7. **The close owns the write (§3.H / the engine boundary).** `run_close` gains the merge as its **first** stage, before the tie-out and the claim-line rebuild — a transaction figure is finalized before anything derives from it. Nothing in a web request mutates `net_eur_eff`: `record_rebate` writes only the rebate registry. The merge summary joins the existing `TRANSPORT_CLOSE_RUN` audit meta and `run_close`'s return dict. A merge refusal propagates to `jobs.run_once`'s whole-session rollback, i.e. "halts on the first failure" for free, per that module's docstring.
8. **The disappearance half of the guard — `missing_source_warnings`, advisory (§4.19).** For the period: the set of `(supplier, country)` with fuel activity **and** at least one recorded rebate in an **earlier** period **and** no recorded rebate for this period. §2.5's "Expected rebate" is the harvested authority for learning the expectation from history and for the word *"missing"*. Surfaced (a) on `merge_period`'s result and thence the close audit meta, and (b) **additively** on `ContractAuditResult.source_warnings`, which is the surface that would otherwise publish list-price analytics silently. It never blocks and changes no euro.
9. **Consumer verification, no forked query.** `contract_audit`'s arithmetic is byte-unchanged; it simply now reads a `net_eur_eff` that can differ from `net_eur`. WO-83's artifacts render from `contract_audit.audit()`, so both quote the post-merge figure by construction (one line source — the structural property WO-83 proved). WO-83's existing `overcharge_evidence_drift` (409) is the **correct** response when a merge lands after a claim froze its `detected_eur`: the demand and its enclosure would otherwise disagree. Note it; do not weaken it.

## Invariants this order must preserve

- **§4.9 Decimal, ROUND_HALF_UP, never float** — every rebate amount, share and effective net is `Decimal`; `q2` is the only rounding, applied to the cumulative allocation and the stored figure. `qty` stays un-quantized (§4.2 row 9 / §4.9's own carve-out).
- **§4.10 the server recomputes** — `net_eur_eff` is never accepted from a client. The route accepts a rebate **document**; the engine derives every effective net.
- **§4.14 no cross-currency sums** — the merge totals `amount_eur` only. A rebate that cannot reach EUR is refused at recording, so a non-EUR figure can never reach the sum.
- **§4.15 FX refuse-never-guess** — ECB units-per-1-EUR, divide; no coverage ⇒ `fx_rate_unavailable` and no row. No invented `stated` rate.
- **§4.16 audit old→new** — every changed `net_eur_eff` and every changed rebate field.
- **§4.19 advisory never blocks** — the missing-source warning is a string on a result; no gate, no status, no euro.
- **§4.20 additive** — `source_warnings` is a new field; no existing wire field changes shape or meaning.
- **§4.1/§4.2 tenancy** — org-scoped queries, `TENANT_MODELS` registration, FORCE RLS in the creating migration, real HTTP tenancy probe.
- **§4.6/§4.7 deny-by-default, structural authorization** — `TRANSPORT_READ` reads / `VAT_WRITE` writes, both already existing. **No permission member is invented.**
- **§10 nothing invented / zero Fleet Fuel bytes** — every definition above carries its §-citation; the one place the spec is silent (the allocation formula) is labelled a DOCUMENTED INTERPRETATION with its reasoning, the WO-82 `_specificity` precedent.

## Database / migration impact

One new tenant table, `vat_off_invoice_rebates`; **table count 81 → 82**, **Alembic revisions 85 → 86**, single head preserved. RLS `ENABLE` + `FORCE` + `tenant_isolation` policy ship in the creating migration (§4.2 set-equality holds or `tests/test_rls.py` fails). No column is added to `fuel_transactions` — `net_eur_eff` already exists and is already NOT NULL; this order only starts writing values other than the identity, so **no backfill is required and no historical row changes until a rebate is recorded and a close runs**. Downgrade drops the table and loses every recorded rebate document; it does **not** revert already-merged `net_eur_eff` values (they are ordinary column data), so a downgrade must be followed by a re-close if the identity is wanted back — stated here rather than attempted, because silently rewriting audited money figures inside a schema downgrade would be worse.

## Testing requirements

`backend/tests/transport/test_wo84_rebate_merge.py`
- `test_wo84_on_invoice_only_leaves_effective_equal_to_invoiced` — hand-computed Decimals; no rebate recorded ⇒ `net_eur_eff == net_eur`, `applied == 0`.
- `test_wo84_off_invoice_only_merges_the_whole_rebate` — hand-computed: 1,000 L, `net_eur` 1,350.00, rebate 50.00 ⇒ `net_eur_eff` 1,300.00, `eur_l_doc` 1.3500, `eur_l_eff` 1.3000, `applied` 0.0500.
- `test_wo84_both_tiers_together_compose` — an on-invoice-discounted `net_eur` **plus** an off-invoice rebate; the identity `applied = eur_l_doc − eur_l_eff` still holds and equals the off-invoice layer alone.
- `test_wo84_allocation_is_pro_rata_by_litres_and_sums_to_the_recorded_rebate_exactly` — three lines of unequal litres; per-line shares hand-computed and their sum **exactly** the recorded EUR (no lost or invented cent).
- `test_wo84_merge_is_idempotent` — second `merge_period` changes no value and writes **no** new audit row.
- `test_wo84_unsourced_rebate_is_refused_with_nothing_mutated` — blank `source_ref` ⇒ `rebate_source_required`; zero rebate rows, zero `net_eur_eff` change.
- `test_wo84_non_eur_rebate_without_a_cached_rate_refuses_per_4_15` — `fx_rate_unavailable`, nothing written; and the companion **allowed** case with a cached rate, asserting `fx_source == "ecb"` and the divided figure.
- `test_wo84_rebate_exceeding_the_net_refuses_and_changes_nothing` / `..._no_litres_to_allocate_refuses`.
- `test_wo84_merge_audits_every_changed_row_old_to_new`.
- `test_wo84_merge_is_org_scoped` — an identical-looking rebate in org B changes zero rows in org A.
- `test_wo84_close_runs_the_merge` — `run_close` performs it; no web request can.
- `test_wo84_contract_audit_findings_are_correct_against_post_merge_prices` — the same term + the same lines: **short discount before the merge, no finding after it**, both euros hand-computed. This is the consumer-impact proof.
- `test_wo84_missing_source_warning_fires_when_a_known_rebate_layer_disappears` + `..._does_not_fire_for_a_supplier_that_never_had_one` (the guard must be falsifiable in both directions).

`backend/tests/transport/test_wo84_rebate_routes.py`
- granted role 2xx / denied role 403 on both verbs; cross-tenant read returns **zero** of B's rows over overlapping data; every refusal code asserted on the wire as `{"detail","code"}`.

`backend/tests/test_tenancy_parity.py` — `vat_off_invoice_rebates` as a real probe.
Postgres gate — `test_rls.py` (set-equality + `relforcerowsecurity`) + the concurrency files on a NOSUPERUSER role; `alembic upgrade → downgrade -1 → upgrade` clean.

## Acceptance criteria (verifiable checklist)

- [ ] With no rebate recorded, `net_eur_eff == net_eur` for every ingested row (unchanged behaviour, asserted).
- [ ] 1,000 L at `net_eur` 1,350.00 + a recorded 50.00 EUR rebate ⇒ after `run_close`, `net_eur_eff == 1300.00`, `eur_l_eff == 1.3000`, `applied == 0.0500`.
- [ ] `POST /api/v1/transport/rebates` with `source_ref: ""` returns 422 `rebate_source_required` and creates **no** row.
- [ ] A `PLN` rebate with no cached ECB rate returns 422 `fx_rate_unavailable` and creates **no** row; the same rebate with a cached rate stores `fx_source == "ecb"`.
- [ ] Running the close twice leaves every `net_eur_eff` byte-identical and adds **no** `transport.rebate_merge` audit row on the second run.
- [ ] A term of `expected_discount_eur_l = 0.05` over the merged line yields **no** `short discount` finding; the same term before the merge yields one worth `0.05 × 1000 = 50.00`.
- [ ] `contract_audit.audit()` returns a non-empty `source_warnings` for a `(supplier, country)` that had a rebate last period, has litres this period, and has no recorded source — and an empty one for a supplier that never had a rebate layer.
- [ ] `alembic heads | wc -l` is 1; `alembic upgrade head && alembic check` clean; `test_rls.py::test_rls_migration_covers_every_tenant_table` green.
- [ ] `role_client("read_only")` gets 403 on `POST /transport/rebates`; `role_client("accountant")` gets 200. Tenant B's rebate id in a tenant-A session ⇒ **404**.
- [ ] `README.md`'s scale line and `tests/test_docs_truth.py`'s table constant move to 82 tables / 86 revisions **in the same commit** as the migration.

## Rollback strategy

Code revert plus `alembic downgrade -1`. The downgrade is written and tested (`upgrade → downgrade -1 → upgrade`), and drops only the new table: no VAT claim, claim line, invoice lock, overcharge claim or audit row is touched by either direction. What a downgrade loses: every recorded rebate document. What it does **not** undo: `net_eur_eff` values already merged by a close — they are ordinary audited column data, and rewriting money inside a schema downgrade is the more dangerous act. Narrow mitigation short of a revert: stop recording rebates (the merge then finds nothing to do — the stage is a no-op for any `(supplier, period)` with no rebate rows) and, if the identity must be restored, delete the rebate rows and re-run the close, which recomputes from `net_eur` and audits the reversal old→new like any other change.

## Documentation to update

`docs/transport/rules.md` (the **R50** row; the R49 and R41 rows gain their post-merge consumer note), `TODO.md` (the WO-84 row, the M5 cell, the suite line — which also re-pins WO-83's un-recorded 36 tests), `README.md` (scale line). No ADR is contradicted: ADR-0023 already scopes this work as *"G4.2, 'Price basis + `net_eur_eff` source guard'"* (line 375) and records the Q8 deferral (line 452); this order is the promised delivery, so those sentences become true rather than needing correction.

## Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic upgrade head && alembic check
python -m pytest tests/transport/test_wo84_rebate_merge.py tests/transport/test_wo84_rebate_routes.py -q
python -m pytest tests/transport/test_wo82_contract_audit.py tests/transport/test_wo83_overcharge_artifacts.py -q   # consumers unbroken
python -m pytest -q                                                              # full baseline
python scripts/pii_scan.py --tree
# DEMONSTRATES the fix — the old identity is provably gone:
grep -rn "net_eur_eff" app/services/transport/rebate.py | head    # a second writer now exists
python - <<'PY'
# the merge arithmetic, hand-checkable at a glance
from decimal import Decimal as D
net, qty, rebate = D("1350.00"), D("1000.000"), D("50.00")
eff = net - rebate
print("eur_l_doc", net/qty, "eur_l_eff", eff/qty, "applied", net/qty - eff/qty)
PY
```
