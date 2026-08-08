**WORK ORDER 91 — G4.6: the diesel excise-duty refund (board G4.6; R42, R53's THIRD framing, R51, R49). Effort M. Priority P1. Milestone M5. Depends on: WO-50 (`fuel_transactions`), WO-83 (the evidence-workbook precedent), WO-85 (the canonical query registry), WO-87 (the R53 framing-separation precedent).**

### Objective and business value

`docs/plan/shared/specs/BA_fleet_fuel.md` §1.2 names diesel excise as **"a second
recoverable-cash stream (diesel excise-duty rebates)"** and §1.3's money-flow
diagram gives it its own branch — `[EXCISE REBATE] per-country litres × rate/1,000 L
→ claim to CUSTOMS (separate regime)`. R42 (§7.4) is the requirement, §2.4's
surface table is the definition, §3.L is the advisory covenant and ARCH_plan G4.6
is the board row. **None of it exists in this tree.** `grep -rn "excise" backend/app`
returns exactly one hit — `app/core/authz.py:69`,
`TRANSPORT_READ = "transport.read"  # fuel/toll analytics, excise (advisory)` — a
permission reserved for a surface that was never built; and
`app/api/routes/transport/__init__.py:12` names the missing module by file name:
*"Future slices (`excise.py` — the remaining ARCH_plan file list) include
themselves HERE"*. `docs/plan/plan-a/wo/WO-90-savings-ui.md:154` records the same
absence from the UI side: *"`/excise` … no route exists for any of them"*.

Who pays. Around seven EU states refund part of the excise duty a commercial
haulier pays on diesel, against litres the haulier already burned and already
has invoices for. The platform holds those litres per country per legal entity
in `fuel_transactions` **already** — validated, deduplicated and product-grouped
— so the entire analysis is a projection over data the client has already paid
to have captured, and the deliverable a haulier actually needs is one spreadsheet
per period to hand to customs. That is a second recoverable-cash stream at
near-zero marginal capture cost, on a regime incumbent full-service providers
cover in only a handful of countries. **And it is the analysis most likely to be
misread**: the figure is *not* an entitlement. The spec is explicit and repeats
itself three times (§3.L, §2.4, R42) that it **asserts NO eligibility** — the
conditions that actually qualify a haulier are deliberately not modelled — so the
governing engineering problem of this order is making that limitation structural
rather than a caveat a downstream surface can drop.

### The harvested definitions, cited

Every definition below is quoted from `docs/plan/shared/specs/BA_fleet_fuel.md`
or `docs/plan/plan-a/ARCH_plan.md`; nothing is paraphrased into a rule.

| # | Definition | Citation (verbatim) |
|---|---|---|
| 1 | **Which states** — seven: BE, FR, IT, SI, HU, ES, HR | §2.4 `/excise` row: *"7 countries (BE·FR·IT·SI·HU·ES·HR)"*; Appendix B `EXCISE`: *"Countries: BE, FR, IT, SI, HU, ES, HR"* |
| 2 | **The mechanism** — litres × a per-1,000-litre rate | §2.4: *"`litres × rate/1,000L`"*; §1.3: *"per-country litres × rate/1,000 L"*; R42: *"`litres × rate/1,000 L`"* |
| 3 | **The grain** — per (entity × country) | R42: *"as a parallel claim engine over the same validated diesel lines, **per (entity × country)**"*; ARCH G4.6: *"per (entity × country)"* |
| 4 | **The rate default** — €30.00/1,000 L, an explicit PLACEHOLDER | §2.4: *"rate default **€30/1,000 L is an explicit PLACEHOLDER** in the reported €25–33 band, admin-overridable"*; Appendix B: *"Default rate EUR 30.00 / 1,000 L (PLACEHOLDER in the reported EUR 25-33 band)"* |
| 5 | **Rates are admin-overridable and change** | R42: *"Rates are admin-overridable"*; §1.3/§9.2 item 13: *"The excise rates are a single €30/1,000 L placeholder for all seven countries. Who owns the real per-country statutory rates, and how often do they change (quarterly, per the research)?"*; §6.1: *"not harmonised, rates change quarterly"* |
| 6 | **The evidence packet, and who it is filed with** | §2.4: *"Separate regime, filed with **CUSTOMS**"*; R42: *"with an Excel packet for customs"*; §1.2: *"**Customs authorities** — separate regime for diesel-excise rebates"*; §6.1: *"a **different regime**, claimed from **CUSTOMS**"* |
| 7 | **The source lines** — the same validated DIESEL lines | R42: *"over the same validated diesel lines"*; §6.1: *"Diesel excise ('professional diesel')"* |
| 8 | **WHAT THE FIGURE DOES NOT ASSERT** | §3.L: *"`excise.py` \| **Asserts NO eligibility** (vehicle ≥7.5t / carrier registration not modelled); rates are indicative defaults."*; R42: *"the figure **asserts NO eligibility** — surfaced loudly"*; §6.1: *"trucks **≥ 7.5 t**"*; §9.2 item 14: *"**Who confirms eligibility for excise** (vehicle ≥ 7.5 t, carrier registration)? It is deliberately not modelled."* |
| 9 | **The framing** | §2.4's framing table: *"peer benchmark / excise / refund estimate → **"Indicative / advisory — verify before relying"**"*; R53 verbatim: *"peer/excise/estimate = 'indicative, verify'"* |
| 10 | **The acceptance test** | R42: *"The UI shows the indicative-rate and eligibility caveats on **every surface that shows the number**."* |

**The two named conditions are exactly two, and the spec names them both twice:**
**vehicle weight ≥ 7.5 t** and **carrier registration**. Nothing else is claimed
to be the qualifying set, because the spec claims nothing else — §9.2 item 14
files the whole question as an open owner decision. The service therefore says
what is *not* modelled and stops there (§10: no invented functionality).

### Current-state recon (verified before writing this order)

1. **The permission is already reserved.** `app/core/authz.py:69` carries the
   comment *"fuel/toll analytics, excise (advisory)"*. WO-79 recorded the
   reservation, WO-81/87 claimed it for their read surfaces. **No permission
   member is added by this order** (§10).
2. **The route package already names `excise.py`.** `app/api/routes/transport/__init__.py`
   docstring, verbatim: *"Future slices (`excise.py` — the remaining ARCH_plan file
   list) include themselves HERE, not in `app/api/router.py`."* `app/api/router.py:89`
   agrees: *"transport route slice (claims today; fuel/recovery/excise later)"*.
3. **The `invalid_period` defect is wider than WO-90 reported.** The single wire
   code is raised by **seven** services carrying **three** different sentences:
   `use YYYY-MM` (`savings`, `contract_audit`, `rebate`, `receipt_control`,
   `tie_out`, `statement_ingest`), `use YYYY-Q1..YYYY-Q4 or YYYY-YEAR`
   (`claim.validate_ref_period`), and `use YYYY-MM, YYYY-Q1..YYYY-Q4 or YYYY-YEAR`
   (`fuel.resolve_period_months`). The SPA maps that one code to the **claim**
   sentence only, so **four month-shaped pages already render a wrong instruction
   today** — `Savings.tsx`, `Overcharges.tsx`, `Rebates.tsx`, `VatAdmin.tsx` —
   and `fuel.py`'s either-shape message has no correct rendering at all.
4. **README's table count is a hard-pinned literal, not a derived one.**
   `backend/tests/test_docs_truth.py:127`: `assert readme_tables == 82, "table
   count claim moved — recount __tablename__ across app/models"`. A new tenant
   table must move README **and that literal** in the same commit. The other
   README counts are derived — and `_py_module_count` globs the **top level
   only**, so a model/service/route inside `transport/` moves none of them. The
   Alembic revision count IS derived: 88 → 89.
5. **There is no generic per-org settings table in this codebase.** `app/models/`
   has no settings/preferences model. The established precedent for
   admin-curated transport configuration is a transport-local tenant table
   (`vat_supplier_contract_terms`, `vat_supplier_cadences`,
   `supplier_vat_registrations`) with audited `set_*`/`remove_*` verbs. So a new
   tenant table is genuinely required, and it follows that precedent exactly.
6. **`queries.price_comparison_transactions` cannot be reused.** Its docstring
   forbids the parameter this order needs: *"There is deliberately NO `supplier`
   parameter … A consumer that wants one supplier's findings filters the
   FINDINGS, never the rows."* It also has no `entity_id`. Excise is grained
   **per entity**, and an entity filter genuinely cannot change any other cell's
   answer, so it gets its own named cut that delegates to `queries.fuel_transactions`.
7. **Excise reads no money column at all.** It reads `qty` (litres) and
   multiplies by a EUR rate. So §4.14/§4.15 land *differently* here than in
   `savings.py`, which refuses on `fx_source == "unknown"`: there is nothing to
   convert, so refusing would be theatre. That divergence is stated in the module
   docstring and proven by test, never silently inherited.
8. **`fuel_transactions.entity_id` FKs to `issuer_profiles`** (composite
   `(org_id, entity_id)`), read through `app.services.issuer.get_by_id` — the
   ADR-P3 rule 2 path `claim.py`/`fuel.py`/`claim_pack.py` all use. The
   "per entity" grain is the issuer-profile grain.

### Scope

**In scope:**
- `app/models/transport/excise_rate.py` (**new**) — `VatExciseRate`, the
  admin override of a country's per-1,000-litre rate. Tenant-scoped.
- `alembic/versions/<rev>_excise_rates.py` (**new**) — the table + **FORCE RLS in
  the same migration** (§4.2).
- `app/core/tenant.py` — register the model.
- `backend/tests/test_tenancy_parity.py` — a real probe (not an exemption) in the
  same commit that creates the table.
- `app/services/audit.py` — two new actions for the rate CRUD.
- `app/services/transport/queries.py` — `excise_transactions`, the named cut.
- `app/services/transport/excise.py` (**new**) — `excise_report(period)`,
  `rate_for`, `list_rates`, `set_rate`, `remove_rate`,
  `build_evidence_workbook(period)`, and the eligibility/framing constants.
- `app/schemas/transport_excise.py` (**new**) — Decimal-typed, strings on the wire.
- `app/api/routes/transport/excise.py` (**new**) + the package aggregator.
- `frontend/src/lib/transportClaims.ts`, `frontend/src/components/RefusalNotice.tsx`,
  and the four month-shaped pages — the `invalid_period` split (Part 4).
- `README.md` (table count 82 → 83, Alembic 88 → 89) and
  `backend/tests/test_docs_truth.py`'s pinned literal, **in the migration commit**.
- `docs/transport/rules.md` (the G4.6/R42 row + R53's third consumer), `TODO.md`,
and `docs/DECISIONS-NEEDED.md` §14 (the two open owner questions §9.2 items 13
and 14 record, plus the lapsed-regime consequence of the country gate).

**Out of scope (named, with the board that owns it):**
- **An `/excise` SPA page.** Board G4.6's UI half; R42's acceptance line (*"the UI
  shows the … caveats"*) is served on the wire by this order — every response and
  the workbook carry the constants — and the page is a follow-up UI slice with
  the WO-90 precedent. This order adds **no SPA page** (README's page count does
  not move).
- **Excise as a claim LIFECYCLE.** R42 says *"parallel claim engine"* of the
  analysis kind; `overcharge.py`'s `detected→…→recovered` state machine is
  G4.5's, and no excise lifecycle is harvested anywhere in the spec. Not invented.
- **Real per-country statutory rates.** §9.2 item 13 is an open owner question
  (*"Who owns the real per-country statutory rates"*). This order ships the
  spec's own placeholder and the override mechanism, and states the placeholder
  as one.
- **Modelling eligibility.** §9.2 item 14 is an open owner question. This order
  states what is not modelled; it does not model it.
- **A `/export/excise` hub entry** (§ Appendix "Export" list) — the export hub
  does not exist in this tree at all.

### Files to touch

| File | Change |
|---|---|
| `backend/app/models/transport/excise_rate.py` | **new** — `VatExciseRate` |
| `backend/alembic/versions/<rev>_excise_rates.py` | **new** — table + FORCE RLS |
| `backend/app/core/tenant.py` | register `VatExciseRate` |
| `backend/tests/test_tenancy_parity.py` | `@probe("vat_excise_rates")` |
| `backend/tests/test_docs_truth.py` | pinned table literal 82 → 83 |
| `README.md` | scale line: 82 → 83 tables, 88 → 89 revisions |
| `backend/app/services/audit.py` | `TRANSPORT_EXCISE_RATE_SET` / `_REMOVE` |
| `backend/app/services/transport/queries.py` | `excise_transactions` |
| `backend/app/services/transport/excise.py` | **new** — the analysis + the packet |
| `backend/app/schemas/transport_excise.py` | **new** |
| `backend/app/api/routes/transport/excise.py` | **new** |
| `backend/app/api/routes/transport/__init__.py` | include the router |
| `backend/tests/transport/test_wo91_excise.py` | **new** — the analysis |
| `backend/tests/transport/test_wo91_excise_rates.py` | **new** — the rate registry |
| `backend/tests/transport/test_wo91_excise_eligibility.py` | **new** — the honesty constraint |
| `backend/tests/transport/test_wo91_excise_packet.py` | **new** — the workbook |
| `backend/tests/transport/test_wo91_excise_routes.py` | **new** — the routes |
| `frontend/src/lib/transportClaims.ts` | split the `invalid_period` sentence |
| `frontend/src/components/RefusalNotice.tsx` | `periodShape` prop |
| `frontend/src/pages/{Savings,Overcharges,Rebates,VatAdmin}.tsx` | pass `periodShape="month"` |
| `frontend/e2e/{savings,vat-claims}.spec.ts` | assert BOTH sentences |
| `docs/transport/rules.md`, `TODO.md` | the boards, LAST |

### Implementation guidance

**1. The rate store (`VatExciseRate`).** Natural key `(org_id, country)`;
`rate_eur_per_1000l Numeric(12, 4)`. `Numeric(12,4)`, not `(14,2)`, for
`contract_term.py`'s stated reason — *"These are €/L RATES, not amounts"* — and
because §9.2 item 13 anticipates real statutory rates that are not round euros.
DB CHECKs: `rate_eur_per_1000l > 0` and `country <> ''`. Composite
`UniqueConstraint(org_id, id)` per §4.3. FORCE RLS in the same migration.

**2. Which countries may carry a rate.** `set_rate` accepts **only** a country in
the harvested seven (`REFUND_COUNTRIES`), refusing anything else 422
`excise_country_not_supported`. **Documented interpretation, fail-CLOSED:** the
seven-country list is the spec's statement of *which states operate the regime*;
accepting an eighth would make the platform assert a refund regime exists where
the spec records none. The consequence — a state whose regime lapses cannot be
switched off, only re-rated — is a named follow-up, not a silently-invented
`active` column.

**3. Rate resolution (`rate_for`).** An override row wins; otherwise
`DEFAULT_RATE_EUR_PER_1000L` (`Decimal("30.00")`) for a country in the seven;
otherwise **`None`**. `None` means *no rate is configured for this state*, and a
cell with no rate produces **no finding at all** — never a €0.00 row. A zero
would read as "this state refunds you nothing", which is a different and
materially false claim.

**4. The analysis (`excise_report`).** Gate order, fails CLOSED: module
entitlement → `period` shape (`YYYY-MM`, `invalid_period`) → optional `country`
shape. Rows come from `queries.excise_transactions` (diesel only, one month,
optional entity/country scope) — **no row-selection predicate is written in this
module** (R51/WO-85's AST scan). Group by `(entity_id, country)`; sum `qty`
exactly (never quantized — §4.2 row 9); drop a cell whose rate is `None` or whose
litres are `<= 0`; then, once:

```
indicative_excise_eur = q2(litres / Decimal(1000) * rate)
```

One `q2`, ROUND_HALF_UP, from the exact litre total — never from an already
rounded intermediate. The entity's display name comes from
`issuer.get_by_id` (ADR-P3 rule 2), never a cross-domain join.

**5. §4.14 / §4.15 — stated, because they land differently here.** This module
reads **`qty` and nothing else**: no `net_local`, `gross_local`, `net_eur`,
`vat_eur`, `net_eur_eff` or `currency` amount is read, so no amount is ever
summed across currencies and **no FX rate is applied anywhere**. A PLN-invoiced
diesel line contributes its litres exactly like a EUR one, and an
`fx_source == "unknown"` row is *not* a refusal here — unlike `savings.py`, which
must refuse because it compares euros. Proven by a structural test that asserts
the money column names are absent from the module source, and by a behavioural
test with a PLN/unknown-provenance line in scope.

**6. THE ELIGIBILITY LIMITATION — structural, not editorial.** Four independent
mechanisms, each proven by a test, mirroring WO-87's R53 treatment:
   1. **One server-side constant**, `ELIGIBILITY_STATEMENT`, in the service. It
      names the two conditions the spec names and denies the entitlement reading
      in the spec's own posture. Every response schema and both workbook sheets
      render **that constant** — there is one string, so it cannot be softened
      downstream. `RATE_CAVEAT` does the same for the placeholder rate.
   2. **`eligibility_asserted: bool` is a required field on every result shape
      and every response schema, and it is a literal `False`.** A consumer cannot
      render the number without receiving the denial beside it.
   3. **Vocabulary**: no field name in the service or the schemas contains any
      of WO-87's `CLAIM_WORDS` (`recover`, `owed`, `owes`, `claim`, `demand`,
      `due`, `debt`, `payable`) — the word list is **imported from
      `tests/transport/test_wo87_r53_framing.py`**, never re-typed, so the two
      surfaces cannot drift apart. The euro is `indicative_excise_eur`: the
      qualification is in the field name itself.
   4. **A seeded-violation self-test** (`WORK_ORDER_TEMPLATE.md` rule 6) for each
      scanner — a deliberately claim-shaped field name, and a shape missing the
      constants, are both detected.

**7. The packet — one source, two renderers (the WO-74/WO-83 precedent).**
`build_evidence_workbook` calls **`excise_report`** and renders its result; the
renderer is a **sync** function taking an `ExciseReport` and holding no
`AsyncSession`, so it structurally cannot reach a second source. One `_COLUMNS`
spec drives the sheet. Refuses 422 `no_excise_findings` rather than emitting an
empty workbook — a customs packet with no litres in it is misleading, not empty.
Free-text cells go through the one shared `core.csv_safety.sanitize_cell`
(CWE-1236); numeric cells stay raw `Decimal` per that module's own rule.

**8. Routes.** `prefix="/transport"`, router-level `TRANSPORT_READ`;
`GET /transport/excise`, `GET /transport/excise/packet`,
`GET /transport/excise/rates` on it, and `PUT`/`DELETE /transport/excise/rates`
with a per-route **`VAT_WRITE`** override — the **existing** permission
`overcharges.py` already uses for contract-term configuration (`_WRITE`). No
permission is invented. Thin controllers: parse → call the already-gated service
→ shape. Every refusal is the service's own `AppError`.

**9. Part 4 — the `invalid_period` split. No wire slug changes.** The wrong
sentence lives in `frontend/src/lib/transportClaims.ts`, not on the wire: the
server already sends the correct `detail` per service, and the map overrides the
title/next with a claim-shaped instruction. So the split is **fully additive
(§4.20)** — zero backend codes change, zero backend tests change:
   - `REFUSALS.invalid_period` keeps the **claim** sentence, unchanged, correct
     for the claim routes.
   - A new `PERIOD_SHAPE_REFUSALS` table adds the **month** sentence.
   - `claimRefusal(code, detail, periodShape?)` and `RefusalNotice`'s new
     `periodShape` prop select it; the default stays `"claim"`, so no existing
     call site changes meaning.
   - The four month-shaped pages pass `periodShape="month"`.
   Both sentences are asserted in Playwright (`savings.spec.ts` for the month
   shape, `vat-claims.spec.ts` for the claim shape).

### Invariants this order must preserve

- **§4.1/§4.2/§4.3/§4.4 (tenancy).** `vat_excise_rates` carries `org_id` on every
  query, is registered in `TENANT_MODELS`, ships **FORCE RLS in its creating
  migration**, has the composite `(org_id, id)` unique, and gains a **real parity
  probe** in that same commit (never an `EXEMPT` row). A cross-tenant rate is
  invisible, and the analysis over org A never reads a litre of org B.
- **§4.6/§4.7 (authorization).** Reads on the reserved `TRANSPORT_READ`; the two
  writes on the existing `VAT_WRITE`. Structural, declared on the router.
  No `Permission` member is added.
- **§4.9 (Decimal).** Litres are exact and never money-quantized; the euro is one
  `q2` from the exact litre total; storage is `Numeric`; no float anywhere; every
  figure crosses the wire as a string.
- **§4.10 (the server recomputes).** The workbook's TOTAL row is the report's own
  recomputed total, never a client figure and never a second summation.
- **§4.14 / §4.15.** Nothing is summed across currencies because **no currency
  amount is read**; no FX rate is applied, so there is nothing to guess.
- **§4.16 (audit).** `set_rate`/`remove_rate` audit old→new in the same
  transaction. The analysis and the packet mutate nothing and audit nothing —
  they are reads (`claim_pack`/`overcharge_pack` precedent).
- **§4.19 (advisory).** §3.L, verbatim: the excise seam must be *"structurally
  incapable of gating or mutating a legal figure"*. It gates no claim, touches no
  VAT figure, writes no `fuel_transactions` column, and a test compares every
  transaction row before and after a run.
- **§4.20 (wire).** Every response declares a `response_model`; refusals are
  `{"detail","code"}`. **No existing slug changes** — Part 4 is additive.
- **§9 / §10.** Every constant is cited above; the two open owner questions
  (§9.2 items 13 and 14) are stated, not decided in code.

### Database / migration impact

One new tenant table, `vat_excise_rates`:

| Column | Type | Note |
|---|---|---|
| `id` | GUID PK | `UUIDPrimaryKeyMixin` |
| `org_id` | GUID FK `organizations.id` ON DELETE CASCADE | indexed |
| `country` | `String(2)` | one of the harvested seven (service-gated) |
| `rate_eur_per_1000l` | `Numeric(12, 4)` | CHECK `> 0` |
| `created_at`/`updated_at` | tz-aware | `TimestampMixin` |

Constraints: `UNIQUE(org_id, country)`, `UNIQUE(org_id, id)`,
`CHECK(rate_eur_per_1000l > 0)`, `CHECK(country <> '')`. **RLS
(`ENABLE` + `FORCE` + `tenant_isolation` policy) is created in the same
migration.** No backfill: the seven defaults are code constants, so an org with
no row behaves exactly as the spec's placeholder describes. Downgrade drops the
table and its policy and loses only the overrides — the defaults are unaffected,
and no money figure is rewritten. Single head preserved.

### Testing requirements

`backend/tests/transport/test_wo91_excise.py`
- `test_wo91_recoverable_is_litres_over_1000_times_the_rate` — hand-computed
  Decimal: 1,234.567 L at €30.0000/1,000 L → `Decimal("37.04")` (exact:
  37.03701, one ROUND_HALF_UP).
- `test_wo91_the_grain_is_entity_times_country` — two entities × two countries
  → four cells, each with its own litres.
- `test_wo91_a_country_with_no_configured_rate_yields_no_finding` — LV diesel in
  scope produces **no row**, and `indicative_excise_eur` is absent, not `0.00`.
- `test_wo91_non_diesel_is_excluded` — AdBlue/Toll/Parking litres never counted.
- `test_wo91_an_admin_override_takes_effect` — `set_rate("FR", "22.5000")`
  changes the euro and the reported rate; `remove_rate` restores the default.
- `test_wo91_set_rate_refuses_a_country_outside_the_seven` (422
  `excise_country_not_supported`) and `test_wo91_set_rate_refuses_a_non_positive_rate`
  (both sides: `0.0001` accepted, `0` refused 422 `invalid_excise_rate`).
- `test_wo91_no_currency_amount_is_read` — structural: the money column names do
  not appear in `excise.py`.
- `test_wo91_a_foreign_currency_line_contributes_its_litres_unconverted` — a PLN
  line with `fx_source="unknown"` is counted and nothing refuses (§4.14/§4.15).
- `test_wo91_the_analysis_mutates_nothing` — every `fuel_transactions` column
  identical before and after (§4.19).
- `test_wo91_invalid_period_is_refused` — `"2026-13"` → 422 `invalid_period`.
- `test_wo91_org_scoping` — org B's litres never enter org A's report.

`backend/tests/transport/test_wo91_excise_eligibility.py`
- `test_wo91_the_eligibility_statement_names_both_unmodelled_conditions`
- `test_wo91_every_result_shape_and_schema_carries_the_constants`
  (`eligibility`, `rate_caveat`, `legal_framing`, `eligibility_asserted is False`)
- `test_wo91_the_excise_surface_carries_no_claim_back_vocabulary` (word list
  **imported** from `test_wo87_r53_framing.CLAIM_WORDS`)
- `test_wo91_the_scan_would_catch_a_claim_shaped_field_name` (seeded violation)
- `test_wo91_the_constant_scan_would_catch_a_shape_missing_the_statement`
  (seeded violation)
- `test_wo91_the_route_paths_carry_no_claim_vocabulary`
- `test_wo91_the_third_framing_differs_from_the_other_two`

`backend/tests/transport/test_wo91_excise_packet.py`
- `test_wo91_the_workbook_reproduces_the_analysis_cell_for_cell` — openpyxl parse.
- `test_wo91_the_workbook_prints_the_eligibility_and_rate_caveats_on_the_numbers_sheet`
- `test_wo91_the_renderer_holds_no_session` — structural one-source proof.
- `test_wo91_an_empty_period_refuses_rather_than_emitting_a_workbook` (422
  `no_excise_findings`).
- `test_wo91_free_text_is_formula_injection_safe` — a `=cmd()` station/entity name.

`backend/tests/transport/test_wo91_excise_routes.py`
- granted role 200 / denied role 403 on each route (read and write separately);
- `module_not_enabled` 403;
- cross-tenant: another org's entity id → opaque **404**, and its rates absent;
- every Decimal on the wire is a **string**;
- `PUT`/`DELETE /transport/excise/rates` refused to a `TRANSPORT_READ`-only role.

`backend/tests/test_tenancy_parity.py` — `@probe("vat_excise_rates")` with
overlapping data in both orgs.

`frontend/e2e/savings.spec.ts` — the month sentence renders on a month-shaped
page; `frontend/e2e/vat-claims.spec.ts` — the claim sentence still renders on a
claim-shaped page.

### Acceptance criteria (verifiable checklist)

- [ ] `GET /api/v1/transport/excise?period=2026-05` returns per-(entity × country)
      rows whose `indicative_excise_eur` equals `q2(litres/1000 × rate)`, every
      figure a JSON **string**.
- [ ] A diesel line in **LV** (outside the seven) produces **no row** — not a row
      with `"0.00"`.
- [ ] `PUT /api/v1/transport/excise/rates {"country":"FR","rate_eur_per_1000l":"22.5000"}`
      as an admin returns 200 and changes the FR figure; the same call as a
      `TRANSPORT_READ`-only role returns **403**; `{"country":"LV",...}` returns
      **422 `excise_country_not_supported`**; `{"country":"FR","rate_eur_per_1000l":"0"}`
      returns **422 `invalid_excise_rate`** while `"0.0001"` returns 200.
- [ ] `DELETE /api/v1/transport/excise/rates?country=FR` restores €30.0000.
- [ ] `GET /api/v1/transport/excise/packet?period=2026-05` returns an
      `openpyxl`-parsable workbook whose detail rows and TOTAL equal the JSON
      response cell for cell, and whose numbers sheet prints
      `excise.ELIGIBILITY_STATEMENT` and `excise.RATE_CAVEAT`.
- [ ] A period with no findings returns **422 `no_excise_findings`**, not an
      empty workbook.
- [ ] Every excise response carries `eligibility`, `rate_caveat`,
      `legal_framing` and `eligibility_asserted: false`; no field name anywhere
      in `excise.py` or `transport_excise.py` contains a `CLAIM_WORDS` token, and
      the seeded-violation self-tests detect a planted one.
- [ ] Org B's id passed to org A's session yields **404**, never 403; org B's
      rates never appear in org A's list.
- [ ] `test "$(alembic heads | wc -l)" -eq 1`; `alembic upgrade head && alembic check`
      clean; `tests/test_rls.py::test_rls_migration_covers_every_tenant_table` green
      on Postgres; `tests/test_tenancy_parity.py` green with the new probe.
- [ ] The SPA shows *"Use a month such as 2026-04"* on a month-shaped page and
      *"Use a quarter such as 2026-Q2, or a year such as 2026-YEAR."* on a
      claim-shaped page; **no backend refusal code changed**.
- [ ] Full backend suite green with the baseline (2192 passed / 10 skipped)
      unchanged except for the new tests.

### Rollback strategy

Code revert plus `alembic downgrade -1`. The downgrade is written and exercised
(`upgrade → downgrade → upgrade`); it loses only the per-country rate overrides,
and because the defaults are code constants the analysis keeps producing exactly
the spec's placeholder figure. Nothing this order writes touches
`fuel_transactions`, a claim, a lock or a fee, so no effect is one-way. The
narrow mitigation short of a revert: revoke `VAT_WRITE` from the affected role —
the analysis then runs on defaults only.

### Documentation to update

`docs/transport/rules.md` (the G4.6/R42 row; R53's third framing gains its first
consumer), `TODO.md` (the WO-91 row, the M5 cell, the suite line), `README.md`
(the scale line, in the migration commit). No ADR is contradicted: ADR-0023's
canonical-registry rule and ADR-0024's structural-authorization rule are both
followed, and ADR-P3's transport-local-table rule is what the rate store obeys.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic upgrade head && alembic check
python -m pytest tests/transport/test_wo91_excise.py \
                 tests/transport/test_wo91_excise_eligibility.py \
                 tests/transport/test_wo91_excise_packet.py \
                 tests/transport/test_wo91_excise_routes.py \
                 tests/test_tenancy_parity.py tests/test_docs_truth.py -q
python -m pytest -q
python - <<'PY'
# DEMONSTRATION: the figure is litres/1000 x rate, and the limitation is one string.
from decimal import Decimal
from app.core.money import q2
from app.services.transport import excise
print(q2(Decimal("1234.567") / Decimal(1000) * excise.DEFAULT_RATE_EUR_PER_1000L))  # 37.04
print(excise.ELIGIBILITY_STATEMENT)
PY
cd /home/user/Bid_it && python scripts/pii_scan.py --tree
cd frontend && npm run build && npx playwright test e2e/savings.spec.ts e2e/vat-claims.spec.ts
```
