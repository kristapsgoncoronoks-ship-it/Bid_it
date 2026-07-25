# Fleet Fuel & VAT Refund System — Business Analysis for Rebuild

**Analyst:** Senior BA (enterprise finance/accounting systems)
**Repo:** `/home/user/fleet_fuel_system` @ `main` (HEAD `5075e08`)
**Scale:** ~68k LOC Python / 110 modules / `app.py` = 21,476 lines / 211 URL rules (206 `@app.route` + 5 blueprint) /
239 test files, 2,422 tests passing
**Purpose of this document:** reverse-engineer the BUSINESS — problem, actors, capabilities, non-negotiable
domain rules, data model, integrations, compliance constraints, and a prioritized rebuild requirement set.

> This is **not** a code review. Where implementation is cited it is to *anchor a business rule to evidence*.

**Contents**
1. Business purpose & actors · 2. Core business capabilities (nine, refining the "seven delegated works")
· **3. The domain rules that must not be lost (3.A–3.N) — the most important section**
· 4. Data model · 5. Integrations & external surfaces · 6. Compliance / legal / risk
· **7. What a rebuild must do — R1–R76, prioritized** · 8. What is dead weight · 9. Open questions
· Appendix A: route surface · Appendix B: key constants quick-reference

**The five things that matter most, if nothing else survives this document:**
1. **§3.A/§3.C** — the VAT claim gates: 30-Sep-year+1 fatal deadline, €400/€50 minimums in the *right
   currency*, one-invoice-one-submission locks, the single `is_synthetic()` predicate that four gates
   share, and the annual-claim-is-a-mop-up rule.
2. **§3.B** — capture reads the **seller legal entity printed on the invoice** (never the buyer, never a
   factoring entity), per-country, with the **hard fraud-safety invariant**: IBAN/VAT/reg-number are
   *never* auto-updated on an existing supplier.
3. **§3.G** — Decimal ROUND_HALF_UP money and the **NET EUR/L, rebates-applied** price basis.
4. **§3.H** — the engine owns and writes the product data; the app reads it read-only; the legal claim
   record lives in a *separate* store from the monthly-rebuilt analytics store.
5. **§3.L** — every advisory seam (AI, finance, workflow, retention, excise, bank recon) is default-OFF
   and structurally incapable of gating or mutating a legal figure.

---

## 1. BUSINESS PURPOSE & ACTORS

### 1.1 The business being run

Two businesses are fused in one product:

**(a) A cross-border VAT-recovery agency** — the operator files EU VAT refund claims under
**Council Directive 2008/9/EC** on behalf of transport companies, and charges a **contingency fee**
(% of recovered VAT, floored at a per-declaration minimum). Today it serves **five Baltic haulage
entities** (`vat_config.HOME_PORTAL`, `customer_master.CUSTOMERS`):

| Entity | Home MS | Home portal |
|---|---|---|
| «Client-EE» AS | EE | Estonian e-MTA (emta.ee) |
| SIA «Client-LV» | LV | Latvian EDS (eds.vid.gov.lv) |
| UAB «Client-LT-3» | LT | Mano VMI / EPRIS |
| UAB «Client-LT-1» | LT | Mano VMI / EPRIS |
| UAB «Client-LT-2» | LT | Mano VMI / EPRIS |

**(b) A fuel-spend intelligence & recovery platform** — it consolidates line-item fuel/toll purchases
across *every* fuel-card network the fleet uses (Q8/Port One, BP/Aral–B2Mobility, TFC by Moya, E100,
Moeve (ex-Cepsa), DKV, Eurowag/W.A.G.), and monetises the resulting proprietary dataset as price
benchmarking, contract-breach overcharge claim-backs, and a second recoverable-cash stream (diesel
excise-duty rebates).

**Stated thesis** (`docs/STRATEGY.md` §0):
> *Turn a transport company's messy, multi-supplier fuel/toll spend into recovered cash and an
> audit-ready financial record — across every fuel card, automatically — and own the cash-timing of the
> refund by financing it.*

**The moat** (§5): the structured, multi-network, line-item invoice dataset. Captive card schemes see
only their own network; manual VAT agencies never assemble it; telematics vendors do no financial
settlement.

**The repositioning** (§4a, `/value`): the incumbent Eurowag *already* consolidates third-party
invoices, so "multi-card consolidation" is **not** the differentiator. The differentiator is
**independence + analytics**: money an independent partner surfaces that a captive fuel-card scheme
structurally will not (overpay vs the cheapest same-day rival; supplier contract-discount breaches).

### 1.2 Actors / roles

**System roles — four tiers, low→high** (`auth.ROLES` / `ROLE_RANK`, enforced centrally in `app._guard`):

| Role | Business meaning |
|---|---|
| `user` | **The client.** Read-only base. Sees only "open" read-only pages: `/value`, `/fees`, `/claim-status`, home. No codes, no fees, no actions. |
| `processor` | Day-to-day operator: intake, capture review, confirm, master-data upkeep. Capabilities configurable by sysadmin. |
| `admin` | Full **business** administration — the entire VAT-refund module and the CRM are `ADMIN_ONLY`. |
| `sysadmin` | Super-admin: everything always + user management + server setup + the permission matrix. Never configurable. Setup creates the owner as sysadmin; legacy `admin` accounts migrated to `sysadmin` once. |

The matrix (`role_permissions`) is sysadmin-configurable for `CONFIGURABLE_ROLES` = admin/processor/user;
an unset cell falls back to `_DEFAULT_GRANTS` (admin/processor all-on, user all-off).

**Login is by EMAIL** (`auth.resolve_login` → internal `username`, which remains the audit principal /
display name). Email is unique per account and required for new accounts; a bare username still
authenticates for transition safety.

**External parties in the domain model:**
- **Customers / claimants** — the fleet legal entities (`customers.db`). Lifecycle
  `prospect → pending → active → inactive` (`customer_master.CUSTOMER_STATES`).
- **Suppliers / fuel-card issuers** — the *sellers printed on the invoice* (`suppliers.db`), with a
  **per-country legal entity** each (`supplier_vat_registrations`).
- **Refund tax authorities** — home MS portal forwards to the refund MS (Art. 7);
  `customer_master.TAX_AUTHORITY` maps refund country → authority name for generated PoAs.
- **Customs authorities** — separate regime for diesel-excise rebates (`excise.py`).
- **Financing partner** — licensed factoring provider behind `finance.FinanceProvider` (default
  `NullProvider`: **nothing funds, no money moves**).
- **Banks** — refund payout IBAN per customer; advisory bank↔refund reconciliation (`bank_recon.py`).

### 1.3 Money flow

```
Fleet buys fuel/tolls abroad (VAT paid to foreign supplier)
        │
        ├─► [VAT REFUND]  claim filed via HOME portal → refund MS decides (4/6/8 mo)
        │                 → pays within 10 working days → refund lands
        │                     ├─ payout_route = 'customer' → we invoice our fee (receivable)
        │                     └─ payout_route = 'us'       → we deduct fee, remit net
        │                 fee = max(fee_pct% × base, fee_min)   [customer_master.compute_fee]
        │                 rate FROZEN at submission; fee CHARGED on the PAID amount
        │
        ├─► [EXCISE REBATE] per-country litres × rate/1,000 L → claim to CUSTOMS (separate regime)
        │
        ├─► [OVERCHARGE CLAIM-BACK] contract_audit € breach → supplier credit/refund request
        │
        └─► [EMBEDDED FINANCE] advance ~80% of the filed-but-unpaid receivable via a licensed
                               partner; platform earns origination/margin (NOT built — seam only)
```

**Subscription revenue** — `module_pricing.py` → `/modules`: four client-facing packages
(**Analytics · Invoicing · Document management & e-signature · Tax refund**) activated one by one with a
live running EUR/month total. `tax_refund` is **€0/mo** — deliberately monetised as the `/fees`
contingency instead. **Grandfathering rule:** activation *captures* the price
(`module_price_at_<pkg>`); a later operator list-price change never re-rates an active client. Every
list-price change is audited (`module_price_history`). *Prices are indicative — nothing charges anyone yet.*

**Contract number** — `auth.users.user_number` is a unique sequential number assigned at registration;
the client's contract number is `<DDMMYYYY>/<NNNNNN>` (registration date + zero-padded number, e.g.
`26062026/000043`), via `auth.format_contract_number`.

---

## 2. CORE BUSINESS CAPABILITIES

The project self-describes as an **"accounting platform of seven delegated works."** That framing is
sound but **understates the product**: it omits the cash-recovery/analytics surfaces that are now the
strategic centre of gravity. My refinement — **nine capabilities**, with the original seven mapped:

| # | Capability | Original "delegated work" |
|---|---|---|
| C1 | Multi-network invoice capture & validated transaction ledger | 1 Data processing |
| C2 | EU VAT refund claim lifecycle & filing | 5 VAT processing |
| C3 | VAT control / receipt control / reconciliation | 6 VAT control |
| C4 | Cash-recovery intelligence (overpay, overcharge claim-back, excise, ROI dashboard) | *(new — was folded into 2)* |
| C5 | Price-competitiveness & benchmark analytics + report/ERP export | 2 Invoice analytics & export |
| C6 | Client master data / light CRM / onboarding & document generation | 4 Light CRM |
| C7 | Document vault + DMS + secure sharing + e-sign | 3 Digital document storage |
| C8 | Sales invoicing (own service fees + general invoicing) | 7 Invoicing for work |
| C9 | Platform floor: auth, audit, backup, tenancy, secrets, queue/worker | *(the floor under all)* |

### 2.1 C1 — Multi-network invoice capture & validated transaction ledger
**What it does.** Turns supplier statements/invoices arriving in any of six shapes (xlsx, csv, xml, API,
PDF, hybrid Factur-X PDF) into a validated, VAT-decomposed, FX-verified, line-item transaction ledger.
**Why it matters commercially.** This IS the moat. Nothing else in the product works without it, and it
is the asset a captive card scheme cannot assemble.
**Key business rules.** Deterministic-first extraction (§3.I); the ~30-min supplier onboarding contract
(§5.1); the engine tie-out to the invoice document (§2.1a); DELETE-by-period idempotency; the
`net_eur_eff` off-invoice rebate layer (§4.2); a durable "waiting room" queue so bursts never overload
the server; **automated capture runs out-of-band on the worker tier, never in a web request.**

**2.1a The two INDEPENDENT validation regimes** (a rebuild must keep both — they answer different questions):

| Regime | Module | Question | Consequence of failure |
|---|---|---|---|
| **Engine tie-out** | `consolidate.run()` `expected` check | *"Does what we parsed equal what the invoice PDF says?"* | `sys.exit(1)` → **the whole monthly close halts** |
| **Capture review gate** | `validate.validate_batch()` | *"Is this captured batch internally coherent enough to register?"* | `can_commit=False` → **registration blocked at the review screen** |

*(Note: `CLAUDE.md` describes the chain as "consolidate→validate→build_master→history" but `validate.py`
is **not** in that chain — `engine_close.py` never imports it. Two separate regimes.)*

**Engine tie-out metrics** (per supplier, vs figures typed **from the invoice PDF**): `lines`
(**tolerance 0 — exact line count, always**), `gross_local` (0.02–0.05), `net_eur` (0.05), `gross_eur`
(0.05), `diesel_litres`. Fail ⇒ `sys.exit(1)` **after** processing every supplier so the operator sees
all failures at once, and **the pickle is not written.**

**Capture review rules** (`validate.check_line`, verdict lattice `ok < warn < error`):
| # | Rule | Verdict |
|---|---|---|
| 1 | invoice number present | **error** |
| 2 | date matches `YYYY-MM-DD` (empty passes) | warn |
| 3 | country in the 23-country set | warn |
| 4 | net/vat parse as numbers | **error** + early return |
| 5 | `net > 0` | **error** |
| 6 | `vat >= 0` | **error** |
| 7 | `vat <= net` (VAT > 100%) | **error** |
| 8 | `net <= 5,000,000` | warn |
| 9 | **VAT-rate coherence** — `vat/net` within **±0.5pp** of a known rate for that country. `vat == 0` is deliberately SKIPPED (domestic/exempt validated elsewhere). Deliberately a coherence check, not a hard rule, because reduced rates exist. | warn |

`VAT_RATES` is a hard-coded 23-country table with real dual entries encoding business cases: **PL diesel
8%**, **ES gasoleo 10%**, **EE 22→20 rate change**, **FI 24→25.5**.
**Batch tie-out:** `abs(q2(Σ net+vat) − q2(coversheet_total)) <= 0.02` — **the comparison is made on
Decimals** so a diff sitting exactly on the 2-cent boundary never flips on binary-float noise.
**Commit gate:** `can_commit = (errors == 0) and (tie is None or tie["ok"])`. **Warnings never block.**
**Anti-drift:** `extraction_baseline` records a confirmed extraction as known-good; `regression_check`
flags a drift when a re-extraction moves net or vat by more than 0.02.

### 2.2 C2 — EU VAT refund claim lifecycle & filing
**What it does.** Builds, gates, locks, files, tracks, settles and invoices a claim per
(entity × refund country × period). **Why it matters.** It is the revenue engine (contingency fee) and
the legal-risk surface. **Key rules:** all of §3.A, §3.C, §3.D, §3.E.
**Artifacts:** the Excel claim workbook (`build_workbook`), the evidence pack, the fee report, the
claims-overview / readiness / fees-statement / receivables-forecast workbooks.

### 2.3 C3 — VAT control / receipt control / reconciliation
**What it does.** Answers *"did we receive every invoice the suppliers issued?"* (§3.J), reconciles
statements, triages VAT, and reconciles bank credits against expected refunds.
**Why it matters.** A missing invoice is un-recoverable VAT. Cadence × activity is the only way to know
what *should* have arrived.

### 2.4 C4 — Cash-recovery intelligence (the current strategic centre)
Six surfaces, all built on canonical queries (never forked):

| Surface | Business question | Key rule |
|---|---|---|
| `/recovery-dashboard` | *"How much money can we recover, on one screen?"* | Six readiness states: `ready · deadline · missing · below · submitted · paid`. North-star €: recovered, awaiting, claimable, overcharges, **median days-to-refund**. Deadline risk = within **60 days** of 30-Sep. |
| `/value` | *"What money did we find you that a captive card scheme wouldn't?"* | LOGIN-ONLY for any role incl. read-only `user`; admin-only detail links gated in-route. |
| `/claim-status` | *"Where are my claims?"* (client-facing) | Plain-language stages only — **no codes, no actions, no fees**. |
| `/overcharges` | *"What does the supplier owe us for breaching the contract?"* | Lifecycle `detected→packaged→claimed→recovered\|rejected\|written_off`. Two artifacts off ONE source: an Excel evidence packet and a **formal PDF claim letter with a 30-day credit/refund demand**. `recovered_total()` = the booked-cash north star. |
| `/excise` | *"What diesel excise can we reclaim?"* | 7 countries (BE·FR·IT·SI·HU·ES·HR); `litres × rate/1,000L`; rate default **€30/1,000 L is an explicit PLACEHOLDER** in the reported €25–33 band, admin-overridable. **Asserts NO eligibility.** Separate regime, filed with **CUSTOMS**. |
| `/estimate` | *"Upload last quarter → see your refund opportunity"* (acquisition wedge) | In-memory only, **NO product-DB write**. `recoverable_eur = vat_eur` (invoiced VAT assumed recoverable) — **a sales preview, never a filed figure.** Optional prospect handoff. |

**CRITICAL nuance a rebuild must preserve — two different "overpay" numbers coexist and will NOT
reconcile:**
- `queries.q_savings` — **same-day, same-country, cheapest rival** grain. Powers `/savings`,
  `/headtohead`, the home KPI, `metrics.M_OVERPAY`, the summary workbook's Savings sheet.
- `pricing_intelligence.internal_benchmark` — **country × month, best-of-your-own-suppliers** grain.
  Powers `savings_intel.summary`, `/intel`, `/value`.
Different grain, different denominator. **They are supposed to differ. Label them distinctly.**

**Legal framing is deliberately different per analysis and must not be flattened:**
| Analysis | Framing |
|---|---|
| `contract_audit` / `overcharge` | **"Money the supplier owes"** — a claim letter with a 30-day demand |
| `q_savings` / overpay review | **"Negotiation evidence, NOT a contractual claim-back"** — printed on every sheet |
| peer benchmark / excise / refund estimate | **"Indicative / advisory — verify before relying"** |

### 2.5 C5 — Price competitiveness & benchmark analytics
Nine distinct analyses. The business definitions that must survive a rebuild:

| Analysis | Definition | Constant |
|---|---|---|
| **Avoidable overpay (same-day)** | `litres × (this supplier's eff €/L − the cheapest same-day, same-country rival's eff €/L)`, diesel only, **requires ≥2 suppliers that day**, positive deltas only. Attributed to the **country of supply** and the **supplier that charged the premium**. | — |
| **Internal benchmark** | `Σ_supplier qty × (supplier_eff − best_eff)` per **country × month** = *"money you could have saved by routing volume to the cheaper supplier you were ALREADY using."* Self-sourced ⇒ no antitrust exposure. | — |
| **Peer benchmark** | Entity vs the **equal-weight MEDIAN of the OTHER entities** (itself excluded). Deliberately *not* volume-weighted — the robust "can't single out one entity" form. `addressable = gap × qty` only when gap > 0. | **`PEER_MIN_CONTRIBUTORS = 2`** — fewer ⇒ suppressed, "cohort too small". Under multi-tenancy the cohort is restricted **intra-tenant** — a client never sees another client's prices. **This is the antitrust gate.** |
| **Margin report (3 baselines)** | `gap_vs_my` (vs your own benchmark, matched exact date → **±3 days** → city month-avg → country month-avg → `no-benchmark`); `gap_vs_pack` (vs the **volume-weighted** average of the other suppliers in the same city/bucket — a simple mean let a 50 L fill move the pack as much as a 40,000 L fill); `margin_vs_wholesale` (**the TRUE margin/wholesale spread**). Coverage KPI: `matched_litres` vs `unmatched_litres`. | — |
| **Supplier reliability** | *"Does the supplier INVOICE what it ADVERTISED?"* `delta = invoiced − advertised`, advertised carried forward from the latest quote on-or-before the fill date; no prior quote ⇒ **UNMATCHED, excluded from the score**. `reliability_score` = within-tolerance matched fills / matched fills. `advertised_prices` is **append-only, kept forever** so a past invoice can always be re-checked against the price that applied on its date. | **`OVERCHARGE_TOL_EUR_PER_L = 0.01`** |
| **Contract audit** | Two term types only: **`expected_discount_eur_l`** (rebate that should be applied, €/L) and **`max_net_eur_l`** (contracted NET price ceiling, €/L). Flags: `"short discount"` (`applied < expected − tol`) and `"over ceiling"` (`eff_l > max + tol`). `recover_eur = gap × litres`, dropped if ≤ 0. **No volume-tier / stepped-rebate / annual-bonus / card-fee modelling.** | **`TOLERANCE = 0.005 €/L`** (env `AUDIT_TOLERANCE_EUR_L`) |
| **Anomalies** | Six rules — `station_price` (station €/L > mean+2σ of the country's stations, **≥200 L floor**), `price_divergence` (supplier's MoM move diverges >2σ from the **market median move** — not merely moved), `volume_spike` (vehicle vs **its own** trailing volumes), `vehicle_price` (vehicle €/L vs the **fleet's** spread, **≥100 L floor**), `off_period` (date month ≠ loaded period), `off_hours` (diesel at 22:00–04:59 ⇒ possible card misuse). **Design principle: NO absolute price thresholds ever** — every bound is learned from the data's own spread, because fuel prices swing. | **`ANOMALY_SIGMAS = 2.0`**; robust modified-z (Iglewicz-Hoaglin) cutoff **3.5** |
| **Expected rebate** | Learns the typical €/L rebate per (supplier, country) from all history; flags a line where it is **missing** (`abs(rebate) < 0.005`). Rationale: catches rebates that arrive on a **separate invoice we often don't see** (the Q8/Port One case). | — |
| **FX markup trend** | Is this supplier's FX markup creeping up against the market? Sorted **biggest increases first**. | `EPS = 0.1pp` noise floor |

### 2.6 C6 — Client master data / light CRM / onboarding
Onboarding, per-country activation, the adjustable checklist, template document generation, document
requests with a chase board, fee terms, payout routing, expiry tracking (§3.E, §3.F).
**Extensibility seam:** the `extract.py` parser registry / `portal_scraper.py` adapters / `/api/v1/*`.

### 2.7 C7 — Document vault + DMS + secure sharing + e-sign
See §5 and §6 for detail. The unifying design: **five overlay DBs all keyed off one stable
`subject_ref = "doc:<id>"`**, none of which opens a product DB writable.

### 2.8 C8 — Sales invoicing
Legally-compliant outbound invoicing from **multiple** of the client's own companies, each with its own
gap-free number series and logo. See §6.

### 2.9 C9 — Platform floor
Auth/roles/permission matrix, audit, backups + integrity, envelope-encrypted secrets, the durable queue
and worker tiers, multi-tenancy foundation, module on/off switches and commercial packaging.

---
## 3. THE DOMAIN RULES THAT MUST NOT BE LOST

> **This is the most important section of this document.** These are hard-won, non-obvious rules.
> Losing any one of them either forfeits client money, files an invalid claim, or creates a duplicate
> submission. Each is cited to the file/symbol where it lives today.

### 3.A — EU VAT refund mechanics (Directive 2008/9/EC)

**A1. The refund procedure is filed through the HOME member state portal.**
The applicant files ONE electronic application in its own member state; that portal validates and
forwards to the refund state (Art. 7). No paper invoices are sent up front.
→ `vat_config.HOME_PORTAL` (entity → (ISO, portal name)).

**A2. Claim grain = ENTITY × REFUND COUNTRY × PERIOD.**
Period is a quarter (`YYYY-Qn`) or the year (`YYYY-YEAR`). Minimum refund period is 3 calendar months,
maximum 1 calendar year; a shorter period only as the remainder of the year (Art. 16).
→ `vat_refund.vat_applications` PK `(entity, refund_country, ref_period)`; `q_months`, `quarter`.

**A3. Minimum claim amounts — €400 sub-year / €50 full-year (Art. 17), enforced in NATIONAL CURRENCY
where the law fixes a local amount.**
→ `vat_config.MIN_QUARTER = 400.00`, `MIN_ANNUAL = 50.00`.
→ `vat_config.NATIONAL_MINIMUMS = {"Sweden": ("SEK", 4000, 500), "Denmark": ("DKK", 3000, 400)}` —
these are **fixed statutory local amounts, NOT a live FX conversion of the EUR base**.
→ `vat_config.min_for(country, is_annual)` returns `(currency, threshold, basis)` where
`basis == "local"` ⇒ compare the claim's `vat_local`; `basis == "eur"` ⇒ compare `vat_eur`
(euro countries and Poland are *intentionally absent* from the table and fall back to the EUR base).
→ Gate: `vat_refund.below_minimum()` → hard block in `set_status_code` unless an **admin
`override_threshold`**, which is *recorded in the claim's `status_note`* for audit.
**Commercial reason the block exists:** a below-minimum claim would be refused *and* its invoices would
be locked out of the annual mop-up — i.e. the money would be permanently lost.

**A4. The filing deadline is 30 September of the year FOLLOWING the refund year — a fatal time-bar.**
CJEU C-294/11 *Elsacom*: miss it and the right is permanently forfeited.
→ `vat_config.DEADLINE_FMT = "{year_plus1}-09-30"`; `vat_refund.filing_deadline(period)`.
→ `vat_refund.approaching_deadlines(within_days=60)` scans **both** `{today.year, today.year-1}` —
a tight, exhaustive bound, because a period in year Y deadlines on 30-Sep Y+1.
→ `vat_refund.DEADLINE_RISK_DAYS = 60` — inside 60 days of the cutoff a claim is "deadline risk".
**North-star KPI: deadline misses = 0.**

**A5. HARD period-end gate — a claim period cannot be filed before it has fully closed.**
→ `vat_refund.period_end_date(period)` (Q2 → 30 Jun; YEAR → 31 Dec) and `period_ended(period)`
(`today > period_end_date`). Surfaces as checklist item "Claim period ended" and stage `1B`.

**A6. Expenditure (goods) codes per Art. 9 / Reg. (EC) 1174/2009 → Reg. (EU) 79/2012 Annex III.**
→ `vat_config.GOODS_CODE`:
```
Diesel      → "1"  Fuel
HVO         → "1"  Fuel
Promo adj   → "1"  Fuel (price correction)
AdBlue      → "10" Other — operating fluid
Toll/Fees   → "4"  Road tolls and road user charges
Parking     → "10" Other — parking
Service/Other → "10" Other
```
**THE CRITICAL RULE:** an *unknown* product group defaults to code **"10" (Other)** — **NEVER "9"**.
Code 9 is *"expenditure on luxuries, amusements and entertainment"*, the archetypal **non-deductible**
category; filing an operating fluid or parking under code 9 invites refusal.
→ Enforced at `vat_refund.invoice_lines`: `code, desc = GOODS_CODE.get(pg, ("10", "Other"))`, with an
explicit in-code comment stating why. *(Historical note: `docs/MANUAL.md` records that tolls were once
coded `3` and AdBlue/parking `9` — both were bugs that were fixed. A rebuild must not regress.)*
**Known gap to consider:** refund states opting into Art. 9(2) require **sub-codes**; truck diesel
should be **`1.1.2`** (mass > 3,500 kg, diesel). The system emits only top-level `1`.

**A7. Per-invoice application content (Art. 8(2)) is the required output shape.**
Supplier name/address; supplier VAT number *with the refund-state prefix*; invoice date & number;
taxable amount and VAT amount **in the refund state's currency**; deductible VAT amount; deductible
proportion % where applicable; the Art. 9 expenditure code.
→ `vat_refund.invoice_lines()` emits exactly: `supplier, issuer, vat_id, invoice, inv_date, code, desc,
product, currency, net_local, vat_local, net_eur, vat_eur`.

**A8. Art. 10 document thresholds:** the refund state may demand a scanned invoice copy where the
taxable amount is ≥ **€1,000**, and ≥ **€250 for FUEL**. Art. 20(1) additionally allows a demand
regardless of threshold on reasonable doubt. → *Business consequence:* **every original PDF is vaulted,
always** (`invoice_documents`), which satisfies this de facto.

**A9. Decision & payment ladder (Arts. 19–22):** 4 months base → 6 months if additional info requested
→ 8 months maximum on further requests; payment within **10 working days** of decision-deadline expiry.
Interest is owed on late refunds (Arts. 26–27) — **currently NOT tracked; this is recoverable money left
on the table.**

**A10. Art. 20 one-month info-response window is NOT preclusive (CJEU C-133/18 *Sea Chefs*).**
→ Status `2B` (document request received) is modelled as a **soft worklist reminder with an
`action_deadline`, NEVER an auto-reject / forfeiture gate.** `docs/MANUAL.md` explicitly instructs:
*"Do not ever 'harden' it into a forfeiture gate."*

**A11. Deductibility is the REFUND STATE's national law (Art. 5(2)) and does not propagate.**
Two independent clips: (a) refund-state category deductibility, (b) home-state pro-rata (Arts. 6/13).
→ `vat_entitlement.py`: `recoverable_pct(country)` defaults to **100%** (commercial road transport
generally recovers in full), admin-overridable per country via `vat_recover_pct_<country>` app_setting;
`RECOVERABILITY_HINTS` carries BE/FR/ES/IT caveats as **hints to verify, never asserted as law**.
Truck diesel is 100% recoverable in all 12 markets these fleets fuel in — the 50%/exclusion caps attach
to **passenger cars**, not goods vehicles.

**A12. The fuel-card entitlement rule (ECJ C-235/18 *Vega International*, C-185/01 *Auto Lease*, German
BMF Jan-2025).** The **END USER** (the fleet), not the card issuer, is generally entitled to recover;
a national approval "does not apply abroad." Some issuers (DKV, E100) run their own net-invoicing/refund
scheme instead — whether their scheme is a *supply of fuel* (→ 2008/9/EC refund) or a *financial service*
(→ no refund) must be verified **per contract before filing**.
→ `vat_config.COMPLIANCE_NOTES[0]`; `vat_entitlement.ENTITLEMENT_NOTE`.
**This is the commercial justification for rule 3.B below.**

---

### 3.B — "Read the legal entity off the invoice" (the product thesis)

**B1. Capture reads the SELLER legal entity PRINTED ON THE DOCUMENT — never the buyer/client, never a
factoring entity.**
This is a *legal* requirement, not a nicety: Art. 8(2) requires the supplier's name/address and the
supplier VAT number *with the refund-state prefix*, and the entitlement rule (A12) means the invoice
must name the claiming entity as customer.

- **Eurowag/W.A.G.** — `extract._eurowag_seller()` / `extract.parse_eurowag()` read the per-country
  seller from the invoice **footer** (`Pārdevējs / Verkoper: <name…legal form>, <addr>, Uzņēmuma ID …,
  PVN reg. Nr. …: <VAT>`), matched against `_EW_LEGAL_FORM = (BVBA|GmbH|UAB|SIA|s.r.o.|d.o.o.|a.s.|S.A.|AB|SE|BV)`.
  This yields the **local issuing entity per country** (BE BVBA, AT/DE/FR/IT/PL `a.s.`, LT UAB,
  SE AB) — **NOT** the Czech *"W.A.G. Issuing Services, a.s."* **factoring entity** the receivables are
  ceded to. Filing the factoring entity would be wrong on both name and VAT prefix.
- **E100** — `extract._e100_seller_name()` / `_e100_seller_vat()` **anchor** seller name and VAT to the
  `"E100 International Trade"` marker itself, because a generic seller/buyer heuristic was grabbing the
  **buyer's** (client's) LV VAT id from the annexe pages where it repeats.

**B2. Supplier matching is MARKER-ONLY — no fuzzy auto-pairing.**
Matching runs off admin-curated brand / VAT registrations, **country-scoped**
(`supplier_master.add_brand`, `code_for_brand`, `all_brand_map`, `country_from_vat` via
`VAT_PREFIX_COUNTRY`). The detection panel **leads with the legal entity**; the supplier *code* is a
confirm-able **suggestion** a human marks. → `app._captured_entity_html` /
`_supplier_country_registration`.

**B3. PER-COUNTRY ENTITY LEARNING — confirming a statement teaches the master.**
→ `supplier_sync._apply_existing()`. When the invoice's **supply country ≠ the supplier's home
country** (a group issuing through a local legal entity per country):
- the captured seller **SEEDS that country's** `supplier_vat_registrations.entity_name` + `vat_number`
  via `set_vat_registration(source='capture')` — which **never clobbers a curated value**;
- it is **NOT** treated as a change to the **group primary** `legal_name` / home VAT
  (`p["safe_updates"].pop("legal_name"/"address")`, `p["high_risk_changes"].pop("vat"/"company_reg")`),
  so a Belgian Eurowag seller never overwrites the Czech primary, and no spurious home-VAT pending
  change is queued.
- `supplier_master.get_issuer(code, country)` then lands the **right entity on the VAT claim**.

**B4. HARD FRAUD-SAFETY INVARIANT (non-negotiable, `supplier_sync.py` module docstring).**
> *AI verification confirms the capture matches the INVOICE — NOT that the invoice is legitimate.
> A fraudulent invoice with a swapped IBAN or VAT number would PASS verification.*
- `SAFE_FIELDS = ("legal_name", "address", "phone", "email")` — auto-applied **only when**
  AI-verified OR capture confidence is `high`. Fully audited.
- `HIGH_RISK_FIELDS = ("iban", "vat", "company_reg")` — **NEVER auto-updated on an existing supplier**,
  regardless of verification. A detected change becomes a **pending change request**
  (`supplier_change_requests`) requiring explicit admin approve/reject on `/supplier-changes`, which
  shows the **legal entity** and a **view-source link to the vaulted PDF**.
- A brand-NEW supplier MAY be created with its captured IBAN/VAT (nothing to overwrite) but lands
  `status='provisional'` and admin-visible.
- A **sysadmin field gate** (`supplier_fields.capture_allowed(field)`) can deactivate any field so
  OCR/AI capture never writes it.

---

### 3.C — Claim construction & the anti-duplicate / anti-synthetic machinery

**C1. A claim is built ONLY from REGISTERED invoices. One row per (invoice, product code).**
→ `vat_refund.invoice_lines()`. Never an `ALL:` country aggregate.

**C2. `_synthetic(ref, vat_id)` is the single centralized predicate for "not tied to one real invoice":**
```python
("INPUT" in ref) or ref.startswith("ALL:") or (ref == "UNMATCHED") or ("INPUT" in str(vat_id))
```
**A pack containing ANY synthetic line CANNOT be filed.** The same predicate is used by:
- the lock gate (`set_status`, `bad = [... if _synthetic(r)]` → `BLOCKED - unresolved invoice refs`)
- the checklist gate (`submission_checklist`, item *"All invoice refs resolved"*)
- the readiness check (`submission_readiness`)
- the workbook builder (`build_workbook`)
This centralization is deliberate: *"so they all block the same set of synthetic refs."*

**C3. Note→invoice resolution order (`_resolve_inv`) — one source of truth, shared by
`invoice_lines` and `unmatched_lines` "so the two can never drift":**
1. note-match heuristic 1 (note prefix vs registered ref prefix)
2. note-match heuristic 2 (registered ref stem contained in note)
3. **admin-curated note→invoice override** (only *reduces* UNMATCHED; never displaces a successful match)
4. sole-registered fallback (exactly one registered invoice for that supplier+country)
5. else → `"UNMATCHED"` (a **hard block**, not an invented aggregate)

**C4. Note-overrides are validated TWICE — at SET time and at READ time.**
`set_note_override` refuses a non-registered or synthetic target (raises `ValueError`, no row written);
`get_note_overrides` **re-validates against the live registered set** and silently drops a stale one, so
a later de-registration can never inject a non-existent/synthetic ref into a claim. An override changes
**only the invoice ASSOCIATION (bucketing) — never an amount.**

**C5. ONE-INVOICE-ONE-SUBMISSION locks.**
`vat_claimed_invoices` with `UNIQUE (entity, refund_country, supplier, invoice_ref)`. On entering a
locking state (`submitted`/`approved`/`paid`), invoices are locked in the **same transaction** as the
status change (`BEGIN IMMEDIATE`). Lock acquisition uses a **plain INSERT (not INSERT OR IGNORE)** so a
lost race surfaces as `IntegrityError` → **roll back and abort the whole transition** rather than
proceed as if the lock were won.

**C6. Annual claim = the MOP-UP, not the whole calendar year.**
A `-YEAR` claim **excludes** invoices already locked to a quarter (not a conflict — `continue`);
a **quarterly** claim treats any overlap as a duplicate and **blocks**. If a yearly claim has an empty
`claim_set` → `BLOCKED - nothing to claim annually`.

**C7. Only `withdraw_claim` releases locks.**
`rejected`, `3B` (rejection), `3C` (confiscation) and `3D` (under appeal) all **KEEP the locks** —
deliberately, so contested invoices cannot be re-claimed elsewhere and create a duplicate submission.
Reverting out of a locked state without withdrawing is refused:
`BLOCKED - application is '<cur>' and holds invoice locks; use 'withdrawn' to release`.

**C8. Document-presence gate.** Every invoice in the claim set must have ≥1 vaulted document, else
`BLOCKED - physical document missing`. → `docs_index()` (one-query set, avoids N+1).

**C9. RECEIPT-CONTROL WAIVERS — the *only* waivable case (rule "R5 case (a)").**
`_waivable_missing(ref, sup, ctry)` ⇔ the ref is synthetic **AND** starts with `INPUT` **AND** the
supplier has **NO registered invoice at all** for that refund country. That is the genuinely-uninvoiced
supplier ("the invoice isn't coming").
`add_waiver` **REFUSES** a supplier that HAS registered invoices for the country, with the reasoning:
*"an UNMATCHED transaction there is a note-matching fix, not a missing invoice — waiving them would drop
claimable VAT."* A waived supplier's transactions are **excluded from the claim by construction**
(dropped from `invs` before the `bad` gate, `claim_set`, locks, doc-gate and the frozen VAT base), and
the waiver use is stamped into the claim's `status_note` on submission.

**C10. FEE FREEZING — the rate freezes at submission; the fee is charged on the PAID amount.**
- On first entry to a locking state: freeze `vat_eur` **and** `vat_local` computed over **exactly the
  locked `claim_set`** via `invoice_lines` — *not* a raw `SUM(vat_eur)` over the period, which would
  wrongly include period invoices not in this claim. Freeze `fee_pct`, `fee_min`, `fee_eur`.
- On `paid`: recompute `fee_eur = compute_fee(paid_amount or vat_eur, frozen fee_pct, frozen fee_min)`
  and stamp `fee_billed_date`. **Only the fee BASE changes (claimed → paid); the frozen rate/minimum are
  never re-derived.** % / minimum changes only affect *un-submitted* declarations.
- `record_payment` stamps `paid_amount` and drives the claim to `3A` in **ONE transaction** so a crash
  can never leave `paid_amount` stamped while the status/fee lag.

**C11. Fee formula (`customer_master.compute_fee`):** `% fee` takes priority; if it falls below the
per-declaration minimum, the **minimum** is charged. Returns `(fee, basis)` where basis ∈
{`percent`, `minimum`}. Resolution order for the rate (`fee_for`): per-(customer, country) override →
customer default → (0, 0).

**C12. Settlement routes (`settlement`, `customer_master.payout_route`):**
- `payout_to = 'customer'` → refund lands with the client; **we invoice the fee** (fee_receivable).
- `payout_to = 'us'` → refund lands with us; **we deduct the fee and remit the net** to the client.
Fee invoice numbering: `F<year>-<NNNN>`, idempotent, only after `fee_billed_date` exists
(`issue_fee_invoice`).

---

### 3.D — The claim status lifecycle (1A → 5)

`vat_refund.STATUS_LABELS` — **two layers**: a coarse *engine* status that drives locks/fees
(`draft/submitted/approved/paid/withdrawn/rejected`) and a controllable *workflow CODE*.

| Code | Label | Kind |
|---|---|---|
| **1A** | Missing documents | AUTO (system-derived) |
| **1B** | Documents received — period not ended | AUTO |
| **1C** | Can be submitted (with a caveat) | AUTO |
| **1E** | Ready to submit | AUTO |
| 2 | Submitted | manual → engine `submitted` |
| 2A | Successfully submitted | manual → `submitted` |
| 2B | Document request received | manual → `submitted`; carries `action_deadline` |
| 3 | Decision received | manual → `approved` |
| 3A | Money received | manual → `paid` |
| 3B | Rejection | manual → `approved` (**locks KEPT**) |
| 3D | Under appeal | manual → `submitted` (**locks KEPT**); carries `action_deadline` |
| 3C | Confiscation by government | manual → `approved` (**locks KEPT**) |
| 4 | Ready to invoice fee | manual → `paid` |
| 4A | Ready to invoice credit | manual → `paid` |
| 5 | Closed | manual → `paid` |

**D1.** `AUTO_CODES = ("1A","1B","1C","1E")` are **system-controlled — never settable by a user.**
`set_status_code` rejects them: *"is system-controlled — it follows the checklist automatically."*
**D2.** `ENGINE_OF` maps each manual code to the coarse engine state that drives the lock/fee machinery.
**D3.** `derive_stage()`: all non-period checklist items pass? no → `1A`. Period not ended → `1B`.
Verdict has a caveat (not `READY*`) → `1C`. Else → `1E`.
**D4.** The **only legal first manual step** from an unlocked claim is **`2` (Submit)** — anything else:
*"can't set '<label>' before the claim is submitted."*
**D5.** Submission gate order in `set_status_code`: **checklist (1A) → period-end (1B) → national-currency
minimum (Art. 17)** → then `set_status(engine)` which applies synthetic/duplicate/document gates.
**D6.** `suggested_next(code, payout_to)` drives the worklist: `3A → 4A` if payout_to == 'us', else `4`.
**D7.** Rejection **keeps** locks (mirrors 3B's `approved` engine state). Only `withdraw_claim` releases,
and it also NULLs `status_code`.

**Client-facing translation (`CLIENT_STAGES`, `/claim-status`):** internal 1A..5 codes map to
plain-language stages **prep → ready → filed → awaiting → refunded** (plus "needs attention").
**NO codes, no actions, no fees are shown to the `user` role.** This is a deliberate competitive
differentiator (§4a build B: incumbents rarely self-serve a live client status view).

---

### 3.E — The adjustable submission checklist

`vat_refund.submission_checklist()` = the **adjustable customer/country rules** +
**claim-level data checks**. *The user cannot tick these; the system verifies each.*

**Adjustable rules** (`customer_master.checklist_rules`, seeded from `DEFAULT_CHECKLIST`):

| key | label | scope | check_type | ref |
|---|---|---|---|---|
| `contract` | Contract | customer | document | `signed_contract` |
| `customer_data` | Customer data | customer | data | `customer_data` |
| `bank_account` | Bank account | customer | data | `bank_account` |
| `nace` | NACE business activity | customer | data | `nace` |
| `trade_register` | Trade register / company register form | customer | document | `trade_registry` |
| `power_of_attorney` | Power of attorney | **country** | document | `power_of_attorney` |

- `scope` ∈ {`customer` (checked once), `country` (per refund country)}.
- `check_type` ∈ {`document` (a `customer_documents` row of that kind exists **and is still valid**),
  `data` (a built-in verifier in `DATA_VERIFIERS` passes)}.
- `DATA_VERIFIERS`: `customer_data` (reg_number + vat_number + legal_address all present and not an
  `INPUT:` placeholder), `bank_account` (a non-`INPUT` IBAN on file), `nace` (nace_code present).
- **`_field_ok` treats any value containing "INPUT" as MISSING** — the yellow-INPUT placeholder
  convention is a first-class business rule across the system.
- **NACE is required because Art. 11 requires the business-activity description via harmonised NACE codes.**
- **PoA expiry re-blocks claims**: `customer_documents.valid_until` → `expiring_documents(within_days=60)`;
  an expired PoA fails `_has_doc` and the claim drops back to 1A.
- An open PoA **document request** enriches the checklist item's **LABEL** with its status
  ("sent for signature") — **label text only; the boolean `ok` is untouched**
  (`_open_poa_request_note`).

**Claim-level items appended by `submission_checklist`:**
1. *"Receipt control: required invoices received"* (+ names the missing suppliers)
2. *"All invoice refs resolved (no INPUT/aggregate placeholders)"* (the non-waivable synthetics)
3. *"All invoice documents attached"*
4. *"Claim period ended"*

**Invariant stated in code:** every synthetic ref is covered by exactly one of items 1 & 2 —
waivable+unwaived → named in item 1; waivable+waived → excluded entirely; not waivable → blocks item 2.
**Nothing slips through.**

**Activation gates layered on top** (`set_status`, when `gate_activation=True`):
- customer must be `status == 'active'` — *"complete the trade registry, bank account and signed
  contract on the Customers page first"*;
- the **refund country** must be separately activated — *"request and receive the country documents
  (power of attorney)"*.
`set_status_code` passes `gate_activation=False` because the **adjustable checklist supersedes** the
coarse activation flags.

---

### 3.F — Customer lifecycle & onboarding

**F1. `CUSTOMER_STATES = ("prospect", "pending", "active", "inactive")`.**
Every legal/claim gate keys off `status == 'active'` — **a prospect is ignored exactly like a pending
customer.** A PROSPECT is a pre-sale lead (minimal data) created by the `/estimate` acquisition funnel.
- `add_prospect` is **idempotent on company_name** and **never downgrades a real client** of any status.
- `promote_prospect` (prospect → pending) is the onboarding handoff; `set_activation` toggles
  pending ↔ active.
**F2. `EDITABLE_FIELDS` allowlist** — the generic editor and the external CRM-sync API may write only
`company_name, reg_number, vat_number, legal_address, home_portal, phone, email, nace_code,
signatory_name, signatory_title`. **Never** status / fee / payout route / any audited workflow column.
**F3. Country activation is per (customer, refund country)** with its own required-document set
(`country_requirements`, default `["power_of_attorney"]`, catalogue `DOC_KINDS` = PoA, VAT certificate,
tax mandate, fleet list, company extract, signatory ID). `country_ready_to_activate` is
**INFORMATIONAL ONLY — it does not activate and is not a gate**; activation stays an explicit admin click.
**F4. Document-request workflow** (`DOC_REQUEST_STATES` = requested → generated → sent_for_signature →
signed → received) with `DOC_REQUEST_OVERDUE_DAYS = 14` for the chase board.
**F5. Template document generation** — `{{placeholder}}` merge from customer data into .txt/.html/.md/.docx
(+ optional PDF). `merge_fields` deliberately **excludes** `tenant_id`. `TAX_AUTHORITY` maps refund
country → national authority name; an **unknown country yields `""` — the merge never substitutes a guess.**

---

### 3.G — Money, price basis & FX

**G1. Prices everywhere are NET EUR/L, FINAL** — VAT excluded, rebates applied. *This basis must be
stated on any new report surface.* → **NET/effective price = `net_eur_eff / qty`.**
**G2. City dimension = the `station` column.**
**G3. Money quantization (`money.py`) — Decimal, ROUND_HALF_UP, never bare `round()`:**
- `q2(x)` → `Decimal` quantized to cents, ROUND_HALF_UP — **use for threshold decisions**;
- `f2(x)` → the same as a float for SQLite `REAL` storage / JSON;
- `dsum`/`fsum` → exact Decimal summation.
**Rationale (module docstring):** Python's `round()` is banker's rounding (half-to-even), which is wrong
for accounting; and a VAT regime compares totals against **hard EUR thresholds** (€400/€50). Storage
columns stay `REAL`, but *every value written is already exactly quantized* and *every threshold decision
is made on the Decimal form* so a total sitting exactly on a boundary never flips on binary-float noise.
Its own smoke test asserts `q2("399.994") < 400 and q2("399.995") >= 400`.
**G4. `money.D(float)` goes via `repr()`** so `56057.99` stays `56057.99`, not `56057.98999…`.

---

### 3.H — The engine ↔ app write boundary (a data-integrity rule, not an architecture preference)

**H1. The ENGINE owns and WRITES the product DBs** (`fuel_history.db` = validated `transactions` +
master + `settled_metrics`; `suppliers.db` = supplier master). **The app reads them READ-ONLY** via
`dataproduct.connect()` (a `mode=ro` SQLite URI — a stray app-side write raises `OperationalError`).
**H2. VAT claim records live in their OWN database (`vat_claims.db`)**, isolated from the analytics
store that `history.py` rebuilds every month — *"so a monthly reload can never corrupt the legal/financial
claim data"* (`vat_refund.py` module comment). **This is the single most important data-safety decision
in the system.**
**H3. Statement registration is ENQUEUED to the engine worker** (`waiting_room` kind=`register`, actor
propagated), never written in-request.
**H4. The monthly close is an INDEPENDENT entrypoint** `engine_close.py`
(consolidate → build_master → history → run_control → backup), `process_lock`-guarded, one audit trail,
period-stamped pickle, **restartable**.
**H5. Benchmark tables** (`my_prices`, `wholesale_prices`) live in `benchmark.db` (app/portal-owned),
NOT in the engine product DBs.

---

### 3.I — Extraction is DETERMINISTIC-FIRST

**I1. Order of attempt (`extract.extract`):**
1. **Standalone structured e-invoice XML** (UBL / CII) → `parse_einvoice` — **no AI, high confidence**
2. **Embedded XML inside a hybrid PDF** (Factur-X / ZUGFeRD / Order-X / XRechnung) — the probe runs
   **BEFORE** `pdf_text`, the `PARSERS` registry and AI; **the ORIGINAL hybrid PDF is what gets vaulted**
3. `pdf_text` (pdftotext) → **OCR fallback** for scanned/image PDFs (`_MIN_TEXT_CHARS = 24`)
4. **Per-supplier deterministic parser registry** `PARSERS = [parse_eurowag, parse_e100]`
5. **AI backend** — only for an unstructured PDF with no registered parser, **and only when one is
   configured**. `EXTRACT_BACKEND` of `parser` / `none` keeps **every byte on the server.**
A **MIXED** batch (some hybrid, some plain) is split and merged (`_merge_mixed`) — never losing a line/PDF.
**I2. Profile gate** — a line-less **MINIMUM / BASIC-WL** Factur-X profile
(`_PROFILE_NO_LINES = {"minimum","basicwl"}`) is **never trusted as a complete capture**.
**I3. AI never extracts a figure a structured/parser path can.** AI belongs to post-extraction
validation/analytics, not capture.
**I4. The AI CAPTURE pipeline is the deliberate, loudly-gated EXCEPTION** — and it is bounded by four
invariants: **OPT-IN / default-OFF** (`ai_vision_capture_enabled` + a vision backend), **ADVISORY**
(a draft a human still confirms; mutates no figure/DB), **STRICT** (never invents a field), and
**best-effort** (falls back to the OCR→parser→text-AI chain).
`ai_verify.py` is an **INDEPENDENT verify model/provider** — the **PDF is the source of truth**; it may
apply PDF-authoritative corrections and re-verify but **NEVER auto-changes or gates a figure without the
human confirm gate**.
**I5. `ai_review.py` (advisory review assistant) sends DERIVED DATA ONLY — never the PDF, IBAN or a
secret — and never mutates or gates a figure.**
**I6. `confidence.py` governs ONLY whether the advisory AI review RUNS.** It never skips or alters a
deterministic legal gate, and **fails toward doing the review.**
**I7. Deterministic post-capture checks (`capture_checks.py`), advisory only:**
- IBAN — ISO 13616 structure + **ISO 7064 MOD-97** check digits (severity **error**);
- VAT-ID — structural check only. **The live EU VIES lookup is deliberately NOT done inline** (rate-limited
  / frequently unavailable); `vies_check` is offline-graceful, returns "not checked", **never raises/blocks**
  (severity **warn**);
- duplicate — same normalized invoice number + amount, **across ALL five entities**, not just this
  supplier+statement (prior-duplicate = **error**; in-batch repeat = **warn**).
Unknown/uncheckable inputs yield **no finding** — *fail toward not crying wolf.*

---

### 3.J — Receipt control (did we receive every invoice the supplier issued?)

`invoice_control.py`:
1. Each supplier has an **invoicing CADENCE** (`suppliers.invoice_cadence`):
   `semi-monthly` (one invoice per half-month: E100, DKV) · `monthly` (MOEVE, BP, TFC, PORTONE) ·
   `monthly-per-country` (one per month **per country with activity**: Q8).
2. **EXPECTATION = cadence × ACTIVITY.** An invoice is expected for a slot **only if transactions exist**
   in that slot (and country). No activity → **"NO ACTIVITY"**, no invoice expected — OK.
3. **CROSS-CONTROL:** `RECEIVED + DOC` (registered **and** PDF in vault) · `RECEIVED no doc` ·
   `MISSING` (activity but no invoice registered → chase the supplier). Plus an **orphan check** —
   every transaction must be covered by a registered invoice.
4. Results persist in engine-owned `invoice_receipt_control`; **manual overrides (waived / note) survive
   re-runs.**

---

### 3.K — Audit, integrity & evidence

**K1. Every data change is audit-logged with `changed_by`** (`audit.py`, DB triggers calling
`ffs_actor()`); web requests set the actor in `app.py`'s before/after request hooks
(`audit.set_actor` / `reset_actor`).
**K2. Errors self-control to the Admin panel** — a handled failure goes to **BOTH** `applog`
(logs/app.log, dev/ops) **AND** the admin-facing error log (`auth.log_error` → `error_log` in
`security.db`). A global `@app.errorhandler` catches unhandled exceptions; real HTTP 4xx/redirects pass
through and are never logged as errors. **Never `except: pass`.**
**K3. Document integrity** — `vat_refund.verify_documents()` re-hashes the **LIVE** PDF/ZIP store
against `invoice_documents.sha256`. The default **DEEP** pass always re-hashes every file; `quick=True`
is a fast incremental sweep that skips a LOCAL file whose `(mtime,size)` is unchanged vs the advisory
`document_integrity_state` cache — **the cache can NEVER mask a corrupt/missing file** (any mismatch
forces a full hash). Any integrity failure is written to the error log **AND** shown as a red banner.
**K4. Backups** — `backup.snapshot/verify/restore/harden` writes `backups/ffs_*.zip` with a **SHA-256
MANIFEST** over the data DBs + `security.db` + the `documents/` store + audit CSVs. Opt-in scheduler
(`backup_interval_hours`, leader-elected across worker processes); off-machine sync
(`FFS_BACKUP_SYNC_DIR`).
**K5. Compliance AUDIT SNAPSHOT (`audit_snapshot.py`)** — at confirm, a **highlighted duplicate** of each
invoice PDF is vaulted (`kind='audit_snapshot'`): **SUPPLIER details boxed RED, CLIENT details boxed
BLUE** via PDF `/Square` annotations (pypdf coords + pikepdf; combines tm×cm so boxes are correct on
**rotated** pages). It **matches the supplier by VAT / registration number / address — NOT the bare
name.** Best-effort — **never blocks confirm.** No AGPL dependency (pypdf + pikepdf only, no PyMuPDF).
**K6. Evidence pack** — `vat_refund.evidence_pack(entity, refund_country, period)` assembles the filing
bundle. The vault tree is human-navigable and identical across backends:
`Customer (reg no) / Year / Country / Claim period / file`.

---

### 3.L — Advisory-only seams (must never gate or mutate a legal figure)

A recurring, deliberate architectural covenant. **All of these are ADVISORY and default-OFF/Null:**

| Module | Advisory boundary |
|---|---|
| `finance.py` | Origination/modelling only. `NullProvider` default — **nothing funds, no money moves.** Never touches a VAT figure, gate, lock, fee, payment or lifecycle. `financeable()` **reuses** `recovery_report()`'s `outstanding` so the two reconcile exactly. |
| `bank_recon.py` | Advisory bank↔refund matching. Never mutates a VAT figure. |
| `workflow.py` | Advisory approval/routing. **NEVER overrides a VAT legal gate** (checklist/locks/period-end/claim status). A run reaching `approved` changes nothing about a claim. |
| `retention.py` | **Flags** records past retention for human review — **never auto-deletes.** Legal hold supported. |
| `excise.py` | **Asserts NO eligibility** (vehicle ≥7.5t / carrier registration not modelled); rates are indicative defaults. |
| `vat_entitlement.py` | Per-country recoverable % defaults to 100; caveats are **hints to verify, never asserted as law**. |
| `ai_review` / `ai_verify` / `vision_capture` / `capture_confidence` | Never mutate or gate a figure without the human confirm. |
| `confidence.py` | Governs only whether the advisory AI review runs. |
| `mcp_server.py` | **READ-ONLY** (v1 — no write/action tools). Reads product DBs strictly via `dataproduct.connect` (ro). **NEVER raises** (returns `{"error": …}`). No bank/secret data (safe-column selects + defense-in-depth key filter). |

---
### 3.M — Document management, retention & sharing rules

**M1. The vault tree is human-navigable and backend-independent:**
`<Customer> <RegNo> / <Year> / <Country> / <Claim period> / <file>`, plus
`<Customer> <RegNo> / customer-documents / <country|general> / <kind> / <file>`.
`period_label()` maps `2026-05 → Q2`, `2026-Q3 → Q3`, `2026-YEAR → Annual`. Every segment is sanitized
for Windows/SharePoint/FTP safety. Backends: **local** (traversal-guarded on read AND write),
**SharePoint** (MS Graph client-credentials), **FTPS** (explicit TLS is the DEFAULT).
**Locator-prefix routing** (`sp://`, `ftp://`, plain path) means history keeps resolving after switching
the default backend.

**M2. SHA-256 dedup with a deliberate cross-invoice WARNING.** Same file on the **same** invoice →
silently skipped. Same file on a **different** invoice → **allowed but WARNED**:
*"identical file already attached to invoice X — verify correct document."* An identical scan on two
invoices is a likely mis-attachment, **not a hard error** — a human decides.

**M3. Claim-composition re-filing is crash-safe.** When invoices are locked into a claim their documents
move to that claim's period folder (a Q1 invoice pulled into an annual claim moves to `Annual`). Done in
a **DB-safe order — write new copy → repoint row → delete old** — so a crash never leaves the DB pointing
at a missing file. Done **AFTER commit, outside the transaction, best-effort** so it never blocks an
already-recorded status change.

**M4. Retention is ADVISORY — it NEVER auto-deletes and never deletes bytes.** Even the action named
`dispose_review` only *flags*; "dispose" names the review queue, not a deletion.
- **`retention.DEFAULT_RETAIN_YEARS = 10`** — *"EU VAT records under Dir. 2008/9/EC and national VAT law
  are typically retained ~10 years."*
- **`invoicing.RETENTION_YEARS = 5`** — statutory retention for an **issued sales invoice** (LV/EU
  record-keeping), stamped as `retain_until` onto the invoice row at issue.
- Framed as **GDPR Art. 5(1)(e) storage limitation** + **ISO 27001 A.5 records management**.
- **THE SAFE RESOLUTION RULE — never under-retain:** among all applicable policies the **LONGEST
  `retain_years` WINS**; a tag-scoped policy beats an `all` policy only as the tiebreak when retentions
  are equal.
- **LEGAL HOLD OVERRIDES RETENTION EVERYWHERE.** A held document is never past-due and is excluded from
  every disposition-review list. Holds are idempotent; the row is **kept on release** (append-only
  place/release history); every action is audited.

**M5. Classification / DLP — the privacy hard invariant.** `findings` is a JSON list of `{type, count}` —
**the matched VALUE is never persisted or logged.** Ordered scale `public < internal < confidential <
restricted`; a document's label is the MAX implied by its findings. Detector→label:
`iban|bank_account|credit_card → restricted`; `bic_swift|vat_id|email|phone → confidential`;
`personal_name → internal`. Detectors are **sanity-checked so the label is defensible** (IBAN mod-97,
card Luhn). The `ai_external_max_sensitivity` policy **defaults to `restricted` = PERMISSIVE** (external
AI paths are byte-identical by default; an admin must *tighten* it). It **FAILS OPEN** on a scan error /
unclassified document and **FAILS CLOSED** only when a policy is set AND the label exceeds it.

**M6. Versioning: history is never destroyed.** v1 = the original; each upload supersedes the prior;
**reverting is itself a NEW version pointing at the old bytes.** Versioning does NOT touch
`invoice_documents` — that index remains the canonical "current original".

**M7. Share links.** Token = `secrets.token_urlsafe(32)`. **A link stores the vault LOCATOR only — never
bytes, never a caller-controlled path.** Passwords reuse auth's scrypt KDF (no invented crypto),
verified in constant time. **Enumeration-safe: missing / revoked / expired all look identical.**
Gate order: **email capture → NDA → signature.** `VIEW_DEDUP_SECONDS = 600` — a repeat view from the
same (link, email) inside 10 minutes is a refresh: still served, but **not double-counted and not
re-notified**. Watermark **never raises** — any failure streams the ORIGINAL bytes, because *"a watermark
must never break the viewer."* Data-room per-recipient allow-lists **can never widen access** (a stray
doc id is filtered against the room's own ids); **no rows = ALL documents** (backward-compatible).

**M8. E-signature — the legal standard is stated honestly.**
`esign.py` is a **SES (Simple Electronic Signature), explicitly NOT a QES** —
*"eIDAS Art. 3(10): a typed/drawn signature with logged consent and an audit trail; **no certificate or
trust service provider is involved**."* Every signing surface carries that label.
**The integrity anchor:** the signature BINDS `signed_doc_sha256` = SHA-256 of the exact bytes presented
for signing; `verify()` re-hashes both the live signed PDF and the original-as-signed bytes with
constant-time comparison. *"The hash is the tamper-evidence; we invent no crypto."*
**On ANY PDF-stamping failure the signature EVENT is still recorded** — *"a stamping hiccup must never
lose a signature."*
`dokobit.py` is the **QUALIFIED (QES/PAdES) seam** for Baltic eID — **default-OFF**, sandbox base by
default (an unknown value falls back to sandbox, *never the production base by accident*), token sealed
via keyvault envelope encryption and **never logged**. The `type="pdf"` QES mapping carries an explicit
**"FLAGGED for review — confirm against your Gateway contract"** caveat. Smart-ID / Mobile-ID
authentication is a **documented STUB, deliberately not built.**

**M9. Workflow versioning & pinning.** `workflow_versions` is **immutable/append-only**; `update_workflow`
APPENDS and repoints `current_version_id`. **A run is PINNED to its `version_id`** and executes the
pinned steps, **NOT the live workflow — so editing a workflow never changes an in-flight run.** Restore
is a NEW version; history is never rewritten. A `UNIQUE(workflow_id, version_no)` index makes a
concurrent double-edit **RAISE** rather than silently write two rows with the same version number.
**Structural enforcement of the advisory boundary:** `workflow.py` imports only
`applog, audit, db_tuning, db_migrate, tenancy` — **no `vat_refund`, no product DB, no claims DB.**

**M10. Email intake routes on the RECIPIENT TOKEN, never the sender.** *"The sender is forgeable and
forwarding breaks SPF/DKIM, so the token is the client key and the sender is advisory only."* Tokens are
random (`token_urlsafe(9)`) and **rotatable per client — a leaked address is revocable.** The webhook is
HMAC-gated with a keyvault-sealed secret. IMAP pull uses **`BODY.PEEK[]`** so a crashed poll re-reads,
and **`mark_seen` runs only AFTER attachments are enqueued** so an attachment is never silently lost.
All three arrival paths (web upload, email pull/push, e-invoice inbound) converge on **one choke point:
`waiting_room.enqueue`**, where `filesec.scan()` is enforced.

### 3.N — Outbound sales-invoicing rules (a separate legal regime)

**Scope boundary:** `invoicing.py` handles the client's OWN customers (`bill_customers`) — deliberately
**NOT** the platform's `customers.db` (the platform's own clients). *"The two never share a figure, a DB,
or a status code."* A third, separate document is the **service-fee invoice** (`invoice_issue.py`) — what
*we* bill a transport client for the recovery work.

**N1. GAP-FREE NUMBERING IS THE LEGAL CRUX.**
- `next_number()` runs inside a single **`BEGIN IMMEDIATE`** transaction so the read of `last_no` and its
  `+1` bump are serialised — *"two concurrent issues can never read the same last_no and collide or
  skip"*; the loser waits on `busy_timeout` rather than failing.
- Counter key = **`(series, year, tenant_id)`**.
- **A number is assigned ONLY at issue, never to a draft.** Number assignment and the status flip
  **commit atomically together**.
- **Two legal entities must NEVER share a gap-free sequence** — hence the per-company registry, with
  auto-generated non-colliding series codes.
- **Four independent series per company** so counters cannot collide: `INV` invoices, `KR` credit notes
  (*kreditrēķins*), `PROF` proforma, `PIED` quotes. Default format `{date}/{seq}` → e.g. `230626/1`; the
  date only *stamps* the number — **the counter stays per series/year, so gap-freeness is preserved.**

**N2. An ISSUED invoice is IMMUTABLE.** At issue the **issuer and customer are SNAPSHOTTED as JSON** so a
later edit to the issuer profile or the customer book **never rewrites a filed invoice.** All further
edits are refused. The status is re-checked **under the write lock** so there is no double-issue race.

**N3. `validate_for_issue` — the legal gate (a pure read).** Nine checks:
1. must be `draft`; 2. an issuer company must be chosen when companies are registered; 3. that company's
profile must be complete (**legal name + address + VAT number — the Art. 226 set**); 4. **a NON-VAT
issuer cannot legally charge VAT** (no VAT number + `vat_total > 0` ⇒ refused); 5. a customer must be
chosen; 6. **simplified-invoice ceiling `SIMPLIFIED_GROSS_CEILING_EUR = 150.0`** (EU VAT Dir.
Art. 238/226b permits up to €100; **Latvia applies €150**) — over the ceiling with the flag on ⇒ refused;
within it the full customer address/VAT requirement is relaxed; 7. **reverse charge REQUIRES the
customer's VAT number**; 8. ≥1 line; 9. **VAT-IN-EUR rule** — a foreign-currency invoice must also state
the VAT total in EUR, so a **reproducible** FX rate (user-supplied or a cached ECB rate) must exist:
*"We never fabricate one, so refuse to issue until a rate is available."*

**N4. Art. 226 field set is the output contract** — issue date; sequential number; date of supply;
supplier + customer full name/address/VAT id; per-line description, quantity, unit price, rate (+
discount); **per-VAT-rate breakdown (taxable amount per rate, rate, VAT amount)**; exemption /
reverse-charge wording; simplified-invoice label; for a credit note, **a reference to the ORIGINAL
invoice number + date + reason**; payment terms + due date + IBAN.

**N5. `overdue` and `cancelled` are DERIVED at read time, never stored** — so an unpaid invoice past its
due date is always correctly shown overdue **without a background job racing the stored value**; a fully
credited invoice reads `cancelled`.

**N6. Credit notes: never edit an issued invoice.** *"A credit note (kreditrēķins) is the LEGAL way to
reverse or correct an ISSUED invoice — an issued invoice stays IMMUTABLE."* The effect on the original is
**DERIVED, never a mutation**: `credited_total()` sums issued credit notes and the AR view subtracts it.
Guards: cannot credit a credit note; **only an ISSUED invoice can be credited**; a partial credit line
must match an original line and cannot exceed its net; **over-credit guard — existing issued credits +
this one ≤ the original gross**. Inherits the original's VAT rates, reverse-charge flag, currency, fx
rate and simplified flag. UBL type code **381** vs **380**.

**N7. Reverse charge is an EXPLICIT flag, NOT a silent 0%.** `derive_reverse_charge` only computes the
**SUGGESTED default** (both parties VAT-registered, both countries known and DIFFERENT, both in the EU) —
**the route surfaces it as a checkbox the user confirms.** When on, every line is 0% VAT and the
mandatory wording is emitted: *"Reverse charge — VAT to be accounted for by the recipient (Art. 196
Directive 2006/112/EC)."* Non-VAT issuers emit the small-undertaking note (Art. 282–292).
`LV_VAT_RATE_PRESETS = (0.21, 0.12, 0.05, 0.0)` — LV 2026 standard/reduced/reduced/zero; custom rates
still allowed. A **document-level discount is ALLOCATED across VAT rates** so the per-rate breakdown
stays correct.

**N8. Proforma and quote are NOT tax invoices** — own non-legal series, **no legal invoice number
consumed, NO output VAT**, and **excluded everywhere a real invoice is counted** (VAT-output report, AR
aging, revenue, bank matching all filter `doc_type='invoice'`).

**N9. Outbound e-invoice conformance is claimed precisely, per exporter.**
- `invoicing.einvoice_xml` → **PEPPOL BIS Billing 3.0** (`urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0`).
  Tax categories: **`AE` + 0% + mandatory exemption reason** for reverse charge; **`Z`** for other
  zero-rated; `S` otherwise.
- `einvoice_export.py` → **EN 16931 / UBL 2.1** over registered *supplier* invoices, with an honest
  caveat: *"NOT a country-validated e-reporting submission — validate against the destination's EN-16931
  BIS / national CIUS XSD before any legal filing."* **PLACEHOLDER rule:** a genuinely-absent field gets
  a marked `INPUT` placeholder plus an XML comment; **amounts are NEVER placeholdered.**
- **Schematron validation must NEVER produce a false PASS.** `lxml.isoschematron` is deliberately NOT
  used: both official schematrons declare `queryBinding="xslt2"` and lxml only bundles the XSLT **1.0**
  skeleton, so it **silently drops every xslt2-bound rule** (the BR-CO total/category arithmetic,
  codelist lookups) **and reports a FALSE PASS**. Instead a real XSLT 2.0 engine (SaxonC-HE) runs the
  canonical ISO three-stage pipeline. It **fails SOFT**: a missing engine or a genuine engine failure
  returns `ok: None`, never `ok: True`.
- **Factur-X outbound:** the EN-16931 XML is embedded into the PDF as `factur-x.xml` with
  `AFRelationship = Alternative` (via pikepdf); **raises for a draft** (no legal e-invoice); degrades to
  the plain PDF if pikepdf is unavailable.
- **Peppol boundary:** the system produces Peppol-conformant XML but **is NOT a Peppol Access Point.**
  A certified AP (SMP/AS4) is **external infrastructure, explicitly out of scope**; an operator wires the
  AP's delivery webhook to `inbound_einvoice.intake_einvoice(...)`.

**N10. SAF-T ships ONE deliberately GENERIC profile.** `PROFILES = {"OECD": DEFAULT_PROFILE}` with
**placeholder** namespace/version, and a banner emitted **into the file**: *"generic profile; specialize
per jurisdiction before any real tax-authority submission."* The `CountryProfile` seam names three
concrete targets (LT i.SAF-T v2.01, PL JPK — *"a different schema family entirely"*, PT SAF-T).
**RECONCILIATION GUARANTEE:** every ledger row becomes exactly one GL Transaction; **no row is silently
dropped** — a malformed row is skipped AND logged. SAF-T and the ERP CSVs share `queries.q_ledger` so
they reconcile with each other.

**N11. ERP export mappings must be confirmed with the client's bookkeeper.** Stated twice in the code.
The DATEV `BU-Schlüssel` **defaults to empty** precisely because the correct key depends on the client's
chart of accounts and the supply country. DATEV convention: **Umsatz = GROSS**, `S` (Soll/debit) on the
fuel expense account against the supplier creditor Gegenkonto, `;`-delimited with **German comma
decimals**, cp1252. Bill grouping key = **`{supplier}-{date}`** — same supplier + same day = one bill
with many lines.

---

## 4. DATA MODEL (business view)

### 4.1 Database ownership — the write boundary is a business control

| Database | Business content | Written by | App access |
|---|---|---|---|
| `fuel_history.db` | **THE PRODUCT**: validated `transactions`, reporting views, `settled_metrics`, `invoice_receipt_control`, `extraction_baseline` | **ENGINE only** | READ-ONLY (`dataproduct.connect("fuel_history")`, `mode=ro` URI) |
| `suppliers.db` | Supplier master, per-country VAT registrations, brands, products, discount rules, invoice registry, statements | ENGINE close **and** admin CRM in-request | READ-ONLY via dataproduct; writes via `supplier_master.connect()` |
| `customers.db` | Our clients (CRM): profiles, bank accounts, documents, checklist rules, country activations, fees, templates, document requests | app | read/write |
| `vat_claims.db` | **THE LEGAL RECORD**: `vat_applications`, `vat_claimed_invoices` (locks), `invoice_documents` (vault index), `vat_invoice_waivers`, `note_invoice_overrides` | app (`vat_refund.py`) | read/write |
| `benchmark.db` | `my_prices`, `wholesale_prices`, `advertised_prices` | app / portal scraper | read/write |
| `security.db` | Users, password hashes, `app_settings`, `role_permissions`, `error_log`, `api_keys`, tenant registry | app (`auth.py`) | **never committed to git** |
| `intake.db` | Waiting-room job queue | worker | — |
| App-owned runtime DBs | `ecb_rates`, `portal`, `data_lake`, `import_log`, `confidence`, `capture_confidence`, `classify`, `finance`, `overcharge`, `invoicing`, `invoice_issue`, `sharing`, `esign`, `ai_chat`, `search`, `metadata`, `versions`, `retention`, `workflow`, `locks` | owning module | direct |

**The critical isolation:** `vat_claims.db` is deliberately separate from `fuel_history.db` *"so a monthly
reload can never corrupt the legal/financial claim data."* `history.load()` does a **DELETE-by-period +
INSERT** of the whole month — if claims lived in the same store, a re-close would destroy filed claims.

### 4.2 The core business entity — a validated fuel TRANSACTION

Canonical schema, declared (identically, twice — `consolidate.py:34-36` and `history.py:41-43`) as a
**positional row list**. `transactions` in `fuel_history.db`. **No primary key, no unique constraint** —
idempotency is DELETE-by-period only.

| # | Column | Business meaning |
|---|---|---|
| — | `period` | Accounting month `YYYY-MM`. **The partition key** — DELETE-by-period is the idempotency unit. |
| 0 | `entity` | **Our** legal entity that bought the fuel — the VAT-registered claimant / cost centre. A per-supplier constant from the spec, not read per line. |
| 1 | `supplier` | Fuel-card issuer / network code (Q8, BP, TFC, E100, MOEVE, DKV). |
| 2 | `country` | **Country of supply = the VAT jurisdiction.** Drives VAT rate, refund claim country, excise eligibility. Per-line for multi-country suppliers (Q8), else the spec default. |
| 3 | `vehicle` | Vehicle / card identity. Composite and supplier-specific (`card`, `card/plate`, plate, vehicle id) — **not normalised**. |
| 4 | `date` | Fuelling date (ISO). The overpay/head-to-head grain and the FX as-of date. |
| 5 | `time` | Time of fuelling (free text, `""` when unavailable). Fraud/anomaly signal only. |
| 6 | `station` | Station / point of sale. **This is the "city dimension."** |
| 7 | `product` | Product name **as printed on the document** (supplier vocabulary, any language). |
| 8 | `product_group` | Normalised group: `Diesel`, `HVO`, `AdBlue`, `Parking`, `Toll/Fees`, `Promo adj`, `Service/Other`. **Precedence: PROMO → HVO → everything else → Diesel LAST** so "HVO 100" is never mis-grouped as diesel. Multilingual diesel tokens (`DIESEL, ON ACT, GASOLEO, GASOIL, GAZOLE, "ON "`). **This column drives the Art. 9 goods code.** |
| 9 | `qty` | Litres (fuel) / units (AdBlue, parking, tolls). **Deliberately NOT money-quantized.** Denominator of every €/L. Can be 0 (promo correction lines). |
| 10 | `currency` | Document currency (EUR/PLN/SEK). Per-line for multi-currency suppliers. |
| 11-13 | `net_local`, `vat_local`, `gross_local` | Amounts in document currency. `gross_local` is what ties to the supplier invoice total. |
| 14 | `net_eur` | Document net converted to EUR — the as-invoiced net. |
| 15 | `vat_eur` | **Reclaimable VAT in EUR — the VAT-refund north star.** Summed per (entity, country, period) into the claim. |
| 16 | `net_eur_eff` | **Effective net EUR after ALL rebate layers, including OFF-INVOICE ones.** Defaults to `net_eur`. |
| 17 | `note` | **Overloaded**: invoice number ‖ rebate explanation ‖ cash-at-pump flag. **This is the field `_resolve_inv` matches an invoice on.** |
| + | `fx_rate` | The **APPLIED** rate that produced `net_eur` (= `net_local / net_eur`, ECB convention: foreign units per 1 EUR). |
| + | `fx_ecb_rate`, `fx_ecb_date` | The **OFFICIAL** ECB reference rate frozen at load, and the as-of date used. |
| + | `fx_source` | Provenance enum: `eur` \| `ecb` \| **`none`** — no coverage ⇒ **NULL is stored, never a fabricated pass**. |
| + | `tenant_id` | Multi-tenant plumbing, default `'default'`. |

**Reporting views** created with the table: `v_supplier_month` (litres, net, vat, and **both**
`eur_l_doc` and `eur_l_eff`), `v_entity_month` (**the VAT-claim rollup**), `v_station_month`
(**diesel only** — the routing view).

**`net_eur_eff` — the two-tier discount model (a business rule, not a technicality):**
- **On-invoice** discounts (TFC hub discount −0.205/L, E100 station-colour tiers 0.08–0.23/L, MOEVE PRN
  off pump PVP, DKV 1.30 SEK/L + 5.63% service fee) are already inside `net_eur`.
- **Off-invoice** rebates land ONLY in `net_eur_eff`. **The canonical case is Q8/Port One:** Q8 invoices
  at **LIST price** per country; Port One issues a **SEPARATE rebate invoice per country**. The pair must
  always be reconciled. This is the entire reason the `net_eur_eff` column exists.
- **Hazard:** the Q8 rebate layer depends on `month_config.FILES["Q8"]` pointing at the *adjusted*
  workbook. There is no assertion that it does — swapping in the raw file **silently loses the rebate
  layer**, corrupting every price/benchmark/overpay figure.

### 4.3 The VAT claim aggregate

```
vat_applications  (entity, refund_country, ref_period)  ← PK
 ├─ vat_eur, vat_local, currency          [FROZEN at submission over exactly the locked claim_set]
 ├─ status (engine: draft|submitted|approved|paid|withdrawn|rejected)
 ├─ status_code (workflow 1A..5), status_note, decision_date, action_deadline
 ├─ submitted_date, approved_date, paid_date, paid_amount
 ├─ fee_pct, fee_min, fee_eur, fee_billed_date       [rate frozen at submission]
 ├─ payout_to, fee_invoice_no, fee_invoice_date
 └─ tenant_id
      │
      ├──1:N── vat_claimed_invoices   (entity, refund_country, supplier, invoice_ref) UNIQUE
      │           = THE LOCK. Released ONLY by withdraw_claim.
      ├──1:N── vat_invoice_waivers    (entity, refund_country, ref_period, supplier)
      └──   ── note_invoice_overrides (supplier, country, note) → invoice_ref

invoice_documents (entity, supplier, invoice_ref, sha256) UNIQUE
 └─ kind ∈ {original_pdf, scan, ai_extract, audit_snapshot, filing, action, capture_document}
```

Claim lines are **derived, never stored**: `invoice_lines()` computes them live from `transactions`
GROUP BY (note, product_group), resolved to a registered invoice.

### 4.4 Customer & supplier masters

```
customers (code PK, company_name UNIQUE, reg_number, vat_number, legal_address, country,
           home_portal, nace_code, signatory_*, status, fee_pct, fee_min, payout_route)
 ├── customer_bank_accounts (iban PK, purpose='refund payout')   ← the Art. 8(1) IBAN/BIC
 ├── customer_documents (kind, sha256, valid_until)              ← expiry re-blocks claims
 ├── customer_countries (customer, country, status)              ← per-country activation
 ├── customer_fees (customer, country)                           ← per-country fee override
 ├── document_requests (kind ∈ contract|power_of_attorney, status lifecycle)
 ├── checklist_rules (key PK, scope, check_type, ref, active, sort)   ← ADJUSTABLE gate
 ├── country_requirements (country, kind)
 └── doc_templates (name, kind, ext, body)                       ← {{placeholder}} merge

suppliers (code PK, legal_name, group_name, address, home_country, company_reg,
           portal, payment_terms, invoice_cadence, status ∈ active|inactive|provisional)
 ├── supplier_vat_registrations (supplier, country) → vat_number, entity_name, source
 │        ↑ THE PER-COUNTRY LEGAL ENTITY. source='capture' never clobbers a curated value.
 ├── supplier_bank_accounts (iban PK)
 ├── supplier_brands (code, brand, country)          ← marker-only matching
 ├── supplier_products (product_code, product_group, vat_rate, discount_terms)
 ├── supplier_discounts (supplier, country, station_like, product_group,
 │                       expected_discount_eur_l, max_net_eur_l, active)   ← contract terms
 ├── supplier_invoices / supplier_statements / statement_invoices   ← the invoice REGISTRY
 └── supplier_change_requests (supplier, field, old, new, status)   ← HIGH-RISK admin queue
```

### 4.5 Lifecycles at a glance

| Entity | States |
|---|---|
| Customer | `prospect → pending → active → inactive` |
| Refund country (per customer) | (none) → `requested` → `active` |
| Supplier | `provisional → active`, `inactive` |
| Supplier change request | `pending → approved \| rejected` |
| VAT claim (engine) | `draft → submitted → approved → paid`; `withdrawn`; `rejected` |
| VAT claim (workflow code) | `1A/1B/1C/1E → 2 → 2A/2B → 3 → 3A/3B/3C → 3D → 4/4A → 5` |
| VAT claim (client-facing) | `prep → ready → filed → awaiting → refunded` (+ needs attention) |
| Overcharge claim-back | `detected → packaged → claimed → recovered \| rejected \| written_off` |
| Finance advance | `offered → accepted → funded → repaid`; `declined` |
| Document request | `requested → generated → sent_for_signature → signed → received` |
| Sales invoice | draft/proforma/quote → **issued** (immutable) → paid; credit note |
| Intake job | queued → processing → done \| failed (→ DLQ) |

---

## 5. INTEGRATIONS & EXTERNAL SURFACES

### 5.1 Fuel-card networks / suppliers (INBOUND — the moat's raw material)

Seven networks are modelled with real, learned format quirks (`supplier_specs.SPECS`):

| Supplier | Entity | Currency | Cadence | Key quirk |
|---|---|---|---|---|
| **Q8 / Kuwait Petroleum** (+ **Port One** rebate partner) | «Client-EE» AS | multi | monthly-**per-country** | Invoiced at LIST price; **separate Port One rebate invoice per country** → the `net_eur_eff` layer. Per-line country + currency. |
| **BP / Aral (B2Mobility GmbH)** | SIA «Client-LV» | PLN | monthly | Poland **split-payment (MPP) mandatory**; A2 toll ~2.5% ORS fee lines; FX via dated ECB rate with `month_config.FX` fallback. |
| **TFC by Moya** | UAB «Client-LT-3» | EUR | monthly | −0.205/L **only at TFC hubs** (Meer −0.19); third-party stations undiscounted. Flat 21% VAT. |
| **E100 International Trade sp. z o.o.** | UAB «Client-LT-1» | EUR | **semi-monthly** | VAT-inclusive gross; two invoices/month; station-colour discount tiers. Invoice no. carried in `note`. |
| **Moeve Pro Services S.A.U.** (ex-Cepsa) | UAB «Client-LT-2» | EUR | monthly | **ALL amounts VAT-inclusive**; per-line IVA rate (10% gasoleo / 21% EcoBlue); cash-at-pump nets against transfer; 6-dp internal calc. |
| **DKV Euro Service** | «Client-EE» AS | SEK/EUR | **semi-monthly** | Swedish fiscal rep; flat 1.30 SEK/L diesel discount; **5.63% service fee** on parking/services; trusts the supplier's per-line EUR and pro-rates. |
| **Eurowag / W.A.G.** | (parser only) | multi | — | Per-country issuing entity in the footer; **Czech factoring entity must NOT be used.** |

**Onboarding a new supplier is a ~30-minute, documented contract** (`supplier_specs.py:7-22`):
1. Get the first invoice PDF + portal export → 2. Build the standard workbook (`Transactions` sheet) →
3. Copy `_TEMPLATE`, write a ~5-10 line `row_map` → 4. Fill `expected` **from the invoice** (the training
target) → 5. Add the filename to `month_config.FILES` → 6. Run `consolidate.py`: **PASS = trained.**

**Source adapters** (`ingest.ADAPTERS`): `xlsx` (default), `csv`, `xml` (via `safexml` — billion-laughs
defended, because supplier XML is untrusted), `api` (URL + `{PERIOD}` substitution, **token from an
environment variable, never stored in the spec**). The invariant: *the row_map receives a tuple
(xlsx/csv) or a dict (xml/api) — nothing downstream changes.*

### 5.2 Portal scraping (automated capture)

Credential-based scraping (`portal_scraper.py` adapters) for the majority of low-IT suppliers that offer
neither an API nor e-invoicing. **Rules:**
- Runs **OUT-OF-BAND on the worker tier, never in a web request** — enqueued as `waiting_room`
  kind=`fetch` (`KIND_FETCH`).
- Gated by a **per-supplier rate-limiter / concurrency cap / backoff / circuit-breaker**
  (`supplier_rate_limits` / `supplier_rate_state`) — **OPT-IN**; an ungoverned supplier behaves as before.
- An **OFF-by-default scheduler** (`scrape_scheduler_enabled`) auto-enqueues pulls.
- Credentials are **envelope-encrypted** (`keyvault.py`): fresh AES-256-GCM DEK per secret, wrapped by a
  pluggable KEK, AAD-bound to context. Providers: `local` (derived from app secret) or `env`
  (**BYOK**, `FFS_KEK_KEY` / per-tenant `FFS_KEK_KEY_<TENANT>`, fails LOUD if missing).
  **Never log a plaintext secret; GCM auth failures RAISE — never silently return "".**

### 5.3 E-invoicing (both directions)

**INBOUND** — `extract.parse_einvoice`: UBL 2.1 / CII / **Factur-X / ZUGFeRD / Order-X / XRechnung**
hybrid PDFs (EN 16931). Deterministic, **no AI**, high confidence. `_PROFILE_NO_LINES = {minimum,
basicwl}` profile gate. The **ORIGINAL hybrid PDF is what gets vaulted.**
**OUTBOUND** — `einvoice_export.py`: **EN-16931 / UBL 2.1 Invoice XML** export (+ batch ZIP) of
REGISTERED invoices, read-only over `vat_refund.invoice_lines`, NET EUR via `money.f2`; Schematron
validation (`einvoice_validate.py`, `schematron/`).
**Regulatory driver (ViDA):** adopted 11 Mar 2025; mandatory EN-16931 e-invoicing + digital reporting for
intra-EU B2B by **1 Jul 2030**, with national mandates landing 2026–2028 (Belgium 1 Jan 2026, Poland
KSeF Feb/Apr 2026, France Sep 2026, Germany 2027-28, Latvia B2G 2026 → B2B 2028).
**Peppol Access Point is identified as the #1 near-term build and is NOT yet implemented.**

### 5.4 Accounting / ERP exports

`erp_export.py` — native import files off the **same** `queries.q_ledger` source as
`reports.accounting_ledger_csv` (deliberately one source so the three formats cannot diverge):
- **DATEV** Buchungsstapel CSV (`;`-delimited, comma decimals)
- **Xero** Bills template
- **QuickBooks** Bills CSV
Account/tax mappings are configurable `erp_*` app_settings. **Formula-injection-safe.** NET EUR.
`/export/erp?fmt=datev|xero|quickbooks`, surfaced on the `/exports` hub.
Plus **SAF-T (OECD core) XML** (`saft.py`) and the accounting-ledger CSV (19 columns, no preamble lines
because ERP importers choke on them, utf-8-sig).

### 5.5 FX / ECB

`ecb_rates.py` — five sources tried in order (ECB eurofxref XML → ECB SDMX JSON → Frankfurter →
exchangerate.host → open ER-API), first responder wins, order/timeout env-overridable. XML parsed via
`safexml`. Full historical backfill since 1999 so a rate exists for any transaction date. Manual CSV
upload supported. **All sources failing raises, but cached rates keep working.**
**Rate selection rule:** the most recent rate **on or before** the transaction date — i.e. a Sunday
fuelling uses Friday's rate.
**Verification:** `supplier_fx.verify_invoices_fx(period, tolerance=0.02)` — per (supplier, currency,
period, **date**) it compares the *implied* rate (`net_local/net_eur`) against ECB.
`deviation > 0` = the invoice converted at a weaker EUR than ECB, i.e. **it cost the fleet more**.
`no_ref` is reported **separately and is never counted as a pass.** Three thresholds coexist:
`build_master.FX_DEV_AMBER = 1.0%`, `app.FX_DEVIATION_PCT = 2.0%`, `supplier_fx.EPS = 0.1pp` (trend noise floor).

### 5.6 Market price feeds

`market_prices.py` — **deliberately legitimate public sources, not competitor-site scraping**: the
**EU Weekly Oil Bulletin** (official weekly prices *without taxes* per member state) and national
open-data price portals. Env-configured (`MARKET_JSON_URL`, `MARKET_CSV_URL`, `MARKET_SOURCES`,
`MARKET_TIMEOUT=15s`). Diesel only. **If all sources fail it raises a RuntimeError naming every attempt
and tells the admin to upload a wholesale CSV instead** — every benchmark table has a manual
upload/entry fallback.

### 5.7 Banking

`bank_recon.py` — **advisory** open-banking reconciliation: parses a bank CSV (header aliasing for
date/amount/credit/debit/description/counterparty) and matches credits against expected VAT refunds
(`eur_tol=0.01`, `day_tol=14`). Strategy: **partner, don't licence** — become an agent of a licensed
AISP/PISP rather than obtaining a PSD2 licence (Tink / TrueLayer / Yapily shortlisted).

### 5.8 AI seams (all opt-in, default-OFF, advisory)

| Seam | Backend | Data sent | Boundary |
|---|---|---|---|
| `extract._ai_extract` | claude \| openai \| azure | Document **text** (chunked; `AI_DOC_CHAR_BUDGET=60000`, max 8 chunks so no line is silently dropped) | Last resort only; `parser`/`none` keeps every byte on the server |
| `vision_capture.py` | Claude/OpenAI **vision** | PDF **page images** | OPT-IN, ADVISORY, STRICT, best-effort |
| `ai_verify.py` | **INDEPENDENT** model/provider | PDF page images + the draft | PDF = source of truth; never auto-gates a figure |
| `ai_review.py` | configured AI | **DERIVED DATA ONLY** — never the PDF/IBAN/secret | Never mutates or gates |
| `ai_assistant.py` | configured AI | derived data | Advisory chat |
| `classify.py` | — (local) | — | DLP gate: per-document sensitivity label + `{type,count}` findings (**never raw values**); `ai_external_max_sensitivity` policy blocks over-sensitive docs from external AI; **fails OPEN on scan error, CLOSED when a policy is set and exceeded** |
| `render_cache.py` | — | — | Per-process PDF→page-image cache keyed by sha256×cap×dpi so capture & verify render the PDF **once**, not twice |

**Build-vs-buy verdict** (`docs/EXTRACTION_BUILD_VS_BUY.md`): **hybrid, in-house-primary.** Do not
full-outsource OCR — it would route the moat-building corpus through a third party for no real accuracy
advantage, on a problem ViDA is structurally shrinking. Any IDP vendor is a **GDPR Art. 28 processor**
needing a DPA, EU residency and a controlled sub-processor list; **keep SCCs** (the EU-US DPF is under
appeal).

### 5.9 MCP server (AI-agent access)

`mcp_server.py` over `mcp_tools.py`. **READ-ONLY (v1 — no write/action tools).** Reads product DBs
strictly via `dataproduct.connect` (ro). **NEVER raises** (returns `{"error": …}`). Tenant-aware.
**No bank/secret data** (safe-column selects + a defense-in-depth key filter). The `mcp` SDK is an
**optional** extra (`requirements-mcp.txt`) imported only inside `mcp_server` — the app/tests never
need it. stdio transport is trusted; **streamable-HTTP REQUIRES a bearer token** (`FFS_MCP_TOKEN` or an
`api_keys` token).

### 5.10 The n8n / external-automation seam

The **in-app approval workflows are `workflow.py` (keep in-app)**; **n8n is the external
automation/integration layer** (document capture, ERP/e-invoice, bank feeds). It feeds the app through
**ONE authenticated seam** — `POST /api/v1/ingest` (token-gated, scope `api:ingest`; JSON
`content_base64` or multipart `file`) → `waiting_room.enqueue` (KIND_EXTRACT) — and **NEVER touches the
product DBs.** A managed CDR vendor or a self-hosted scan zone may sit in front; **the app re-validates
on return regardless — it never blindly trusts an external verdict.**

### 5.11 Public HTTP surface

| Endpoint family | Auth | Notes |
|---|---|---|
| `/api/v1/*` | **Bearer token only** (`Authorization: Bearer` or `X-API-Key`), scoped, constant-time compare, metered | Scopes: `api:benchmark`, `api:claims`, `api:savings`, `api:crm`, `api:crm.write`, `api:ingest` |
| `/s/<token>`, `/s/<token>/file|event|sign` | **No cookie — token IS the principal** | Public share links; own in-view gate |
| `/r/<token>/…` | token-as-principal | Data rooms |
| `/sso/login`, `/sso/callback` | OIDC | Multi-provider |
| `/dokobit/postback` | provider callback | Baltic eID e-signature |
| `/inbound/email` | **HMAC-gated in-view** | Email intake |
| `/api/pricing`, `/api/vat`, `/api/recovery`, `/api/periods|benchmark|compare|headtohead|entities` | session | Capability-gated |

---
## 6. COMPLIANCE / LEGAL / RISK CONSTRAINTS

### 6.1 VAT & tax law
| Constraint | Source | Where enforced |
|---|---|---|
| Eligibility: taxable person **not established** in the refund state, **no supplies** there in the period | 2008/9/EC Art. 3–4 | *Implicit* (hauliers qualify) — **not modelled** |
| One electronic application via the **home-state portal** | Art. 7 | `vat_config.HOME_PORTAL` |
| Refund period: min 3 months, max 1 year; shorter only as the remainder of the year | Art. 16 | quarterly + annual + dynamic merge |
| Minimum €400 (sub-year) / €50 (full year), or the national-currency equivalent | Art. 17 | `vat_config.MIN_QUARTER/MIN_ANNUAL`, `NATIONAL_MINIMUMS`, `below_minimum()` |
| **30 September of year+1 — a strict, FATAL time-bar** | Art. 15; **CJEU C-294/11 *Elsacom*** | `filing_deadline()`, `approaching_deadlines()`, `DEADLINE_RISK_DAYS=60` |
| Application content incl. **NACE business activity**, the "no supplies" declaration, IBAN + BIC | Art. 8(1), Art. 11 | checklist rules `nace`, `bank_account`, `customer_data` |
| Per-invoice: supplier VAT with refund-state prefix, amounts **in the refund state's currency**, deductible VAT, deductible proportion %, Art. 9 code | Art. 8(2) | `invoice_lines()` |
| Expenditure codes; **code 9 = luxuries/entertainment is never recoverable** | Art. 9; Reg. 1174/2009 → Reg. 79/2012 Annex III | `vat_config.GOODS_CODE`, default `"10"` |
| Scanned copy may be required at **≥ €1,000** general / **≥ €250 for FUEL**; and regardless on reasonable doubt | Art. 10, Art. 20(1) | Every PDF vaulted, always |
| Decision ladder 4 → 6 → 8 months; payment within **10 working days**; **interest owed on late refunds** | Arts. 19–22, 26–27 | Ladder **not modelled**; interest **NOT tracked — recoverable money left on the table** |
| The Art. 20 one-month info window is **NOT preclusive** | **CJEU C-133/18 *Sea Chefs*** | Status `2B` is a soft reminder — *"do not ever harden it into a forfeiture gate"* |
| Deductibility = the **refund state's** national law; home-state pro-rata is a second, independent clip | Art. 5(2), Arts. 6/13; 2006/112 Art. 176 standstill | `vat_entitlement.recoverable_pct` (default 100%, admin override); **home-state pro-rata NOT modelled** — safe only if every entity makes purely taxable transport supplies |
| Fuel card = supply of fuel vs financial service | **CJEU C-235/18 *Vega International*, C-185/01 *Auto Lease***; German BMF Jan-2025 | `COMPLIANCE_NOTES`, `vat_entitlement.ENTITLEMENT_NOTE` — **verify per contract before filing** |
| Sales invoice mandatory content | **2006/112/EC Art. 226** | `invoicing.invoice_text` (annotated field-by-field), `validate_for_issue` |
| Simplified invoice ceiling — Directive permits €100; **Latvia applies €150** | Art. 238 / 226b | `SIMPLIFIED_GROSS_CEILING_EUR = 150.0` |
| Reverse charge wording | **Art. 196** | `REVERSE_CHARGE_NOTE` |
| Small-undertaking exemption | Arts. 282–292 | `NON_VAT_NOTE` |
| Diesel excise ("professional diesel") — a **different regime**, claimed from **CUSTOMS**, trucks **≥ 7.5 t**, not harmonised, rates change quarterly | ETD 2003/96; national law | `excise.py` — **explicitly advisory; asserts no eligibility** |
| Statutory retention: ~10 years (VAT records) / 5 years (issued sales invoice, LV/EU) | national VAT law | `retention.DEFAULT_RETAIN_YEARS=10`, `invoicing.RETENTION_YEARS=5` |
| **ViDA**: mandatory EN-16931 e-invoicing + digital reporting intra-EU B2B by **1 Jul 2030**; national mandates 2026–2028 | Adopted 11 Mar 2025 | `extract.parse_einvoice`, `einvoice_export`, `invoicing.einvoice_xml` |

### 6.2 GDPR
- **The data is personal data.** Fuel statements carry plates, timestamps and locations = **movement profiles**.
  Portal scraping widens the processing footprint and the sub-processor chain.
- **Any AI/IDP vendor is an Art. 28 processor** requiring a **DPA, EU residency, and a controlled
  sub-processor list**. *"Keep SCCs"* — the EU-US Data Privacy Framework is under appeal (Sept 2025).
- **Art. 5(1)(e) storage limitation** is the framing for `retention.py`; **Art. 32** for backup encryption
  (exporter-held key = a Schrems-II supplementary measure).
- **A cross-tenant leak is an Art. 33/34 reportable breach.** The stated test bar is non-negotiable: seed
  tenants A and B with overlapping data, bind A, run the REAL query path, assert A present / B absent,
  then mirror. *"A leak in CI is a release blocker."*
- **Not started:** DPA, Art. 30 RoPA, 72-hour breach runbook, per-tenant export/erasure.

### 6.3 Competition / antitrust — the benchmark is the trap
- Redistributing rivals' live prices is **textbook hub-and-spoke**. Therefore the pooled benchmark is
  **internal-first and counsel-gated to externalise** (Monetisation model ③).
- **Code-enforced controls today:** `PEER_MIN_CONTRIBUTORS = 2` (a cohort of fewer is **suppressed**, not
  shown), and under multi-tenancy `tenancy.scope_clause()` restricts the peer cohort **intra-tenant** —
  a client benchmarks only against its own entities, never another client's prices.
- **NOT code-enforced:** the operator's cross-tenant analytics scope. The in-code policy says owner
  analytics must run on **de-identified/aggregated** data and **must never relay one client's
  identifiable current pricing to another** — but there is **no PII-stripping or aggregation layer**
  implemented. This is a documented commitment, not a control.
- `market_prices.py` is deliberately fed from **legitimate public sources** (the EU Weekly Oil Bulletin,
  national open-data portals) — *"not a tool for scraping third-party/competitor websites."*

### 6.4 Financial-services licensing
- **Factoring is regulated lending** (CRD Annex I; **CRD VI** pulls it further under EU licensing) and is
  **Member-State-specific** — licence-exempt in some EU states, licensable in others (e.g. Germany).
  CJEU holds factoring fees VAT-taxable.
- **Strategy: do NOT take a licence.** Partner with a licensed factoring provider (the Eurowag/Factris
  pattern); monetise **origination/margin only**. `finance.py` therefore defaults to `NullProvider` —
  **nothing funds, no money moves.**
- Open banking: **do NOT obtain an AISP/PISP/EMI licence** — become an **agent of a licensed provider**
  (agent onboarding ~weeks vs many months for direct authorisation); the partner carries PSD2 liability.

### 6.5 Security posture (honest current state)
**Strong:**
- Trigger-based audit inside each DB (survives CLI and manual `sqlite3` edits); thread-local actor
  resolved **per row, per connection**; **BLOB columns structurally excluded** from snapshots, which is
  what keeps password hashes, API-token digests and sealed portal secrets out of the change log.
- scrypt KDF with **transparent rehash-on-login upgrade** (2^14 → 2^16); timing-oracle defence (unknown
  user still runs a full scrypt); constant-time comparison everywhere.
- Lockout: 8 consecutive failures / 15-min window per user (**tenant-scoped**), 25 per source IP.
- Envelope encryption with **AAD binding** — a sealed blob cannot be lifted from one row and replayed
  into another; **per-tenant KEK derivation**; `env`/BYOK **fails LOUD** rather than silently downgrading.
- Strict CSP (`script-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`); global CSRF;
  `/doc/<id>` served inert (attachment + nosniff); session fixation cleared on every login path.
- ZIP caps + structural zip-slip neutralisation (in-memory + `os.path.basename`); Zip-Slip realpath guard
  on restore; **`filesec.scan()` at the single `waiting_room.enqueue` choke point**.
- API tokens: 256-bit entropy, shown once, **SHA-256 only stored**, constant-time verify that **keeps
  scanning after a match so timing doesn't reveal which key matched**, immediate revocation, default-OFF.
- Backups: crash-consistent `.backup()` copies of **26 runtime DBs**, per-file SHA-256 manifest, opt-in
  AES-256-GCM encryption with a key **deliberately separate from `FFS_KEK_KEY`**, off-machine sync,
  documented quarterly restore drill.

**Gaps a rebuild must decide on (ranked):**
1. **Audit has NO hash chain.** Tamper-evidence is snapshot-based only, so detection latency = the backup
   interval — and `backup_interval_hours` **defaults to 0 (off)**. For an evidentiary-grade financial
   record, add a per-row `prev_hash`/`row_hash` chain or an external WORM sink.
2. **The Postgres cutover is structurally blocked.** Modules call `sqlite3.connect(DB)` directly in their
   own `connect()` rather than routing through `db.connect()`, so flipping `DB_ENGINE=postgres` moves
   **no module** today. This gates multi-client SaaS entirely (SQLite's single-writer + noisy-neighbour).
3. **`processor` defaults to the same capability set as `admin`** (`_DEFAULT_GRANTS`). Least privilege is
   opt-in; the only structural separation is `ADMIN_ONLY` route gating.
4. **Password policy is minimum-length-8 only** — no complexity, breach-list, rotation or history.
   2FA is **email OTP only** (no TOTP/WebAuthn) and the same mailbox is the login identifier.
   **No server-side session revocation** — disabling a user does not kill their live 8-hour cookie.
5. **AV fails OPEN by default** — with no clamd configured only EICAR is blocked.
6. **The parse/OCR worker sandbox is documented but not implemented** (an ops concern, but it is the
   single highest-value control: parser CVEs are the main RCE vector).
7. **Portal scraping**: default `local` KEK is derived from the on-disk `.secret_key`, so filesystem
   access ⇒ every stored portal password. Real isolation requires `env`/KMS/BYOK, which is **not** the
   default. Storing fuel-card portal passwords creates a concentrated, high-value credential store — a
   breach exposes **transactional access to client accounts**, not just data.
8. **Portal-scraping ToS/legal exposure is unmitigated** — most fuel-card portals prohibit automated
   access and credential sharing with third parties regardless of account-holder consent; there is no
   consent artifact, warranty or indemnity mechanism. Treat scraping as **transitional with a sunset**;
   the structural de-risker is ViDA-mandated e-invoicing inbound.
9. **`app_settings` are GLOBAL, not tenant-scoped** — module state, pricing and all schedulers. A hard
   blocker for per-tenant packaging and billing.
10. **No billing system** behind `module_pricing` — the commercial layer is state + UI only.
11. **Multi-tenancy activation gate items 3 & 4 are unstarted:** Postgres RLS + a non-owner DB role, and
    the whole P5 posture block (SOC 2 Type II / ISO 27001-27017-27018, pen-test, DPA, breach runbook,
    per-tenant export/erasure).
12. **The review stash uses `pickle`** for uploaded PDF bytes (0700 dir, random server-side token, never
    user-controlled). Trusted-server-data-only today; **never pickle across a broker boundary.**

---

## 7. WHAT A REBUILD MUST DO — prioritized requirements

Legend: **M** = MUST (a rebuild is not the product without it) · **S** = SHOULD · **C** = COULD.
Every requirement is written to be testable.

### 7.1 P0 — Legal correctness of the VAT claim (MUST; these are the product)

| # | Pri | Requirement | Acceptance test |
|---|---|---|---|
| R1 | **M** | A claim is keyed **(entity × refund country × period)**, period ∈ {`YYYY-Qn`, `YYYY-YEAR`}. | Creating two claims with the same key upserts, never duplicates. |
| R2 | **M** | Claim lines are built **only from registered invoices**, one row per (invoice, product code); never an `ALL:` aggregate. | A transaction that resolves to no registered invoice appears as `UNMATCHED`, not an aggregate. |
| R3 | **M** | A **single centralized `is_synthetic()` predicate** (`INPUT` \| `ALL:` \| `UNMATCHED` \| `INPUT` in vat_id) is used by the lock gate, the checklist gate, the readiness check AND the workbook builder. **A pack with any synthetic line cannot be filed.** | Inject one synthetic line ⇒ all four surfaces block with the same message set. |
| R4 | **M** | **One-invoice-one-submission lock**, `UNIQUE(entity, refund_country, supplier, invoice_ref)`, acquired in the **same transaction** as the status change via a plain INSERT; a lost race raises and **aborts the whole transition**. | Two concurrent submissions over an overlapping invoice: exactly one succeeds; the loser's status is unchanged. |
| R5 | **M** | **Only an explicit withdraw releases locks.** `rejected`, rejection (3B), confiscation (3C) and appeal (3D) **keep** them. | After 3B, the invoice cannot be claimed in another period; after withdraw, it can. |
| R6 | **M** | An **annual claim is the mop-up**: invoices already locked to a quarter are excluded (not a conflict); a **quarterly** claim treats any overlap as a duplicate and blocks; an annual claim with an empty set is refused. | Three-case test per the above. |
| R7 | **M** | **Hard period-end gate** — a claim cannot be filed before the period has fully closed. | `today <= period_end_date` ⇒ stage `1B`, submit refused. |
| R8 | **M** | **Minimum-amount gate (Art. 17) enforced in the correct currency**: countries with a fixed national amount compare `vat_local`; all others compare `vat_eur` against €400/€50. Admin override allowed and **recorded in `status_note`**. | SE claim of SEK 3,999 blocked; SEK 4,000 allowed; an EUR-country claim of €399.99 blocked, €400.00 allowed. |
| R9 | **M** | **Filing deadline = 30 Sep of year+1**, surfaced with a **60-day risk window**, scanning both `{today.year, today.year-1}`. | A 2025-Q4 claim shows as at-risk from 2 Aug 2026 and OVERDUE from 1 Oct 2026. |
| R10 | **M** | **Every document is vaulted, always**, and a claim cannot be submitted while any locked invoice lacks a document. | Detach a document ⇒ submit blocked with the invoice named. |
| R11 | **M** | **Unknown product groups map to Art. 9 code `"10"` (Other) — NEVER `"9"`.** Fuel → 1, road tolls → 4. | Assert `GOODS_CODE.get(unknown, ("10", …))`; assert no mapping emits "9". |
| R12 | **M** | **`2B` (document request) is a soft worklist reminder with a deadline — never an auto-reject or forfeiture gate** (*Sea Chefs*). | Passing the `action_deadline` changes no status and blocks nothing. |
| R13 | **M** | **Fee rate FROZEN at first submission; fee CHARGED on the PAID amount.** The frozen VAT base is computed over **exactly the locked claim set**, not a period SUM. `paid_amount` + the paid transition commit atomically. | Change the customer fee % after submission ⇒ the claim's fee is unchanged. Record a partial payment ⇒ the fee recomputes on that amount at the frozen rate. |
| R14 | **M** | **Money uses Decimal ROUND_HALF_UP.** Thresholds are decided on the Decimal form. No bare `round()` on currency. | `q2("399.994") < 400` and `q2("399.995") >= 400`; `f2(2.675) == 2.68`. |
| R15 | **M** | **Receipt-control waivers are permitted ONLY for a genuinely uninvoiced supplier** (synthetic `INPUT` ref AND no registered invoice for that country). Waiving a supplier that HAS invoices is **refused**. Waived suppliers are excluded from the claim by construction and recorded on submission. | Attempt to waive a supplier with a registered invoice ⇒ refused with the "note-matching fix" message. |
| R16 | **M** | **Note→invoice overrides change only the invoice ASSOCIATION, never an amount**, and are validated at BOTH set time and read time against the live registered set. | De-register the target ⇒ the override silently stops resolving; the line reverts to UNMATCHED. |
| R17 | **M** | Pre-submission codes `1A/1B/1C/1E` are **system-derived and not user-settable**; the only legal first manual step from an unlocked claim is **`2` (Submit)**. | Setting `1C` returns "system-controlled"; setting `3A` on an unlocked claim is refused. |

### 7.2 P0 — Capture correctness (MUST)

| # | Pri | Requirement | Acceptance test |
|---|---|---|---|
| R18 | **M** | **Deterministic-first extraction, in this order:** standalone structured XML → **embedded XML in a hybrid PDF (probed BEFORE text/parsers/AI)** → text/OCR → per-supplier parser registry → AI (only if configured). `parser`/`none` backends keep **every byte on the server**. | A Factur-X PDF parses with zero AI calls; the ORIGINAL hybrid PDF is what gets vaulted. |
| R19 | **M** | **A line-less MINIMUM/BASIC-WL e-invoice profile is never trusted as a complete capture.** | A MINIMUM-profile invoice does not auto-confirm. |
| R20 | **M** | **Capture reads the SELLER legal entity printed on the document** — never the buyer, never a factoring entity. Per-country issuing entities must be read per country. | Eurowag BE invoice ⇒ the BE BVBA entity, **not** the Czech "W.A.G. Issuing Services". E100 invoice ⇒ the E100 seller VAT, **not** the buyer's VAT that repeats on annexe pages. |
| R21 | **M** | **Supplier matching is marker-only** (admin-curated brand/VAT registrations, country-scoped). No fuzzy auto-pairing. The UI **leads with the legal entity**; the supplier code is a suggestion a human confirms. | No code path assigns a supplier code without a curated marker or an explicit human confirm. |
| R22 | **M** | **Per-country entity learning:** confirming a statement whose supply country ≠ the supplier's home country seeds **that country's** VAT registration (entity name + VAT, `source='capture'`, never clobbering a curated value) and **does NOT** change the group primary legal name / home VAT nor queue a home-VAT change. | Confirm a BE Eurowag invoice for a CZ-home supplier ⇒ a BE registration appears; the CZ primary is untouched; no pending change is queued. |
| R23 | **M** | **HARD FRAUD-SAFETY INVARIANT: IBAN, VAT number and company registration number are NEVER auto-updated on an existing supplier** — regardless of AI verification. They become pending change requests for explicit admin approval, showing the legal entity and a link to the source PDF. Safe fields (legal name, address, phone, email) auto-update only when verified OR high-confidence. A brand-new supplier may be created with captured IBAN/VAT but lands **provisional**. | Feed a capture with a changed IBAN ⇒ the stored IBAN is unchanged and a pending request exists. |
| R24 | **M** | **Every AI seam is opt-in, default-OFF, advisory, and cannot mutate or gate a figure without a human confirm.** The verify model is **independent** of the capture model and treats the **PDF as source of truth**. | With all AI settings at defaults, the system functions end-to-end with zero external calls. |
| R25 | **M** | **Two independent validation regimes** are preserved: an **engine tie-out to the invoice document** (fail ⇒ the close halts, the pickle is not written) and a **capture review gate** (`can_commit = no errors AND tie-out within 2 cents`, warnings never block). | Line count off by one ⇒ close halts. VAT > net on one line ⇒ registration blocked. Batch tie-out off by €0.03 ⇒ blocked; €0.02 ⇒ allowed. |
| R26 | **M** | **Deterministic post-capture checks are advisory and never gate:** IBAN MOD-97 (error), VAT-ID structure (warn), duplicate invoice **across ALL entities** (error) / in-batch repeat (warn). **A live VIES lookup is never done inline** and never blocks. | A malformed VAT-ID produces a warning, not a block; an unreachable VIES returns "not checked". |
| R27 | **M** | Every upload path (UI, API, email, portal fetch) converges on **one enqueue choke point** where AV scanning runs, **before bytes are written to disk**. | Add a new ingest route ⇒ it must go through the same enqueue or the test fails. |
| R28 | **M** | Uploaded bytes are **fsync'd (file + directory) before the queue row commits**; processing is **at-least-once**; a crash mid-extract leaves a reclaimable lease. This is safe **only because extraction produces a DRAFT and no business data is written until a human (or autopilot) confirms.** | Kill the worker mid-job ⇒ the job returns to `queued` after the lease expires and re-processing double-posts nothing. |

### 7.3 P0 — Data-integrity architecture (MUST)

| # | Pri | Requirement | Acceptance test |
|---|---|---|---|
| R29 | **M** | **The engine OWNS and WRITES the product data; the application reads it READ-ONLY at the connection level** (not by convention). A stray app-side write must **raise**. | An app-path INSERT into `transactions` raises. |
| R30 | **M** | **The legal claim record lives in a SEPARATE store from the analytics/transactions store** that the monthly close rebuilds. | Run a full close ⇒ zero claim rows are touched. |
| R31 | **M** | The monthly close is an **independent, lock-guarded, restartable orchestrator** with **one audit trail** and a **period-stamped, hash-verified** hand-off between stages; it **halts on the first failure**. | Re-running a failed close end-to-end requires no manual cleanup. Editing the period between hand-run steps raises `pickle period X != requested Y`. |
| R32 | **M** | **Every data change is audit-logged with the actor**, resolved **per row on the writing connection** (never a shared/committed context row). Audit travels **inside** each database. CLI/worker writes attribute correctly. | A worker-processed job attributes to the requesting user, not "system". |
| R33 | **M** | **Secret columns must be structurally excluded** from audit snapshots (e.g. store them as BLOB and exclude BLOB from the snapshot expression). | No password hash, API-token digest or sealed secret ever appears in the audit log. |
| R34 | **M** | **Stored secrets use envelope encryption (fresh DEK per secret, wrapped by a pluggable KEK) with AAD binding to context.** A wrong-context or wrong-tenant open **fails the auth tag and RAISES — never silently returns `""`.** Plaintext secrets are never logged. | Move a sealed blob to another row ⇒ open fails. |
| R35 | **M** | **Backups cover every runtime database + the document store + a write-once export of every audit log, with a per-file SHA-256 manifest**; verify and restore are one command each; restore is Zip-Slip guarded. | Corrupt one byte in a snapshot ⇒ `verify` names the file. |
| R36 | **M** | **Document integrity verification re-hashes the live store against recorded hashes.** An incremental fast pass may skip unchanged local files, **but any mismatch forces a full hash — the cache can never mask a corrupt or missing file.** | Truncate a vaulted PDF ⇒ the quick pass still reports CORRUPT. |
| R37 | **S** | **Add tamper-evidence to the audit log** — a per-row hash chain (`prev_hash`/`row_hash`) or an append-only external sink — so an in-place edit is detectable **without** waiting for the next backup. | Edit an `audit_log` row directly ⇒ a chain-verify command reports the break. |

### 7.4 P1 — Commercial surfaces (MUST for the business model)

| # | Pri | Requirement | Acceptance test |
|---|---|---|---|
| R38 | **M** | **Cash-recovery dashboard** bucketing every claim into six readiness states with north-star euros (recovered, awaiting, claimable, overcharges, median days-to-refund, deadline-risk count) — built on the **canonical** claims and recovery queries, **never a forked query**. | The dashboard totals reconcile exactly with the underlying claim reports. |
| R39 | **M** | **Client-facing claim-status portal** with plain-language stages only (prep → ready → filed → awaiting → refunded, + needs attention). **No internal codes, no actions, no fees** are exposed to the client role. | A client-role session cannot see a status code or a fee anywhere. |
| R40 | **M** | **Transparent pricing page + refund calculator** (foreign VAT paid → indicative recoverable − fee = net to you), reusing the same fee function the real claim uses. Standard fee is admin-editable; a per-client fee overrides it. | The calculator and a real claim produce the same fee for the same inputs. |
| R41 | **M** | **Supplier overcharge claim-back**: a per-(supplier × period) lifecycle `detected → packaged → claimed → recovered \| rejected \| written_off`, with an **Excel evidence packet and a formal PDF claim letter (with a credit/refund demand and a deadline) built from the SAME line source**, and a `recovered_total` that feeds the north star. Read-only over the analytics. | Both artifacts for the same (supplier, period) show identical lines and totals. |
| R42 | **M** | **Diesel excise refund** as a parallel claim engine over the same validated diesel lines, per (entity × country), `litres × rate/1,000 L`, with an Excel packet for customs. **Rates are admin-overridable and the figure asserts NO eligibility** — surfaced loudly. | The UI shows the indicative-rate and eligibility caveats on every surface that shows the number. |
| R43 | **M** | **Acquisition funnel:** "upload last quarter → see your refund opportunity" — in-memory parse, **no product-DB write**, per-country aggregation with the threshold flag, and an explicit *"a sales preview, never a filed figure"* caveat. Optional prospect handoff. | Running an estimate writes nothing to the transaction store. |
| R44 | **M** | **Customer lifecycle `prospect → pending → active → inactive`, with EVERY legal/claim gate keyed on `active`.** A prospect is ignored exactly like a pending customer. Prospect creation is idempotent on company name and **never downgrades a real client**. | Submitting a claim for a prospect is refused with the activation message. |
| R45 | **M** | The submission **checklist is adjustable data, not code** — rules with a key, label, scope (customer/country), check type (document/data) and reference, evaluated by the SYSTEM (users cannot tick them). Document expiry (e.g. an expired PoA) **re-blocks** claims. | Deactivate a rule ⇒ it disappears from the gate. Expire a PoA ⇒ the claim drops to stage 1A. |
| R46 | **S** | **Multiple issuer companies**, each with its **own gap-free number series** and logo, chosen per invoice; the issuer and customer are **snapshotted at issue**; an issued invoice is **immutable**; correction is by **credit note**, whose effect is **derived, never a mutation**. | Edit the issuer profile after issuing ⇒ the issued invoice PDF is unchanged. Two companies never share a sequence. |
| R47 | **S** | **Gap-free numbering** assigned only at issue, inside a write-locked transaction, with the number assignment and the status flip committing together. | 50 concurrent issues produce 50 distinct, contiguous numbers with no gaps. |
| R48 | **S** | **Grandfathered pricing:** activating a package captures its price; a later operator list-price change never re-rates an active client; every list-price change is audited. | Raise the list price ⇒ an active client's monthly total is unchanged; deactivate + reactivate ⇒ re-rated. |

### 7.5 P1 — Analytics & intelligence (SHOULD)

| # | Pri | Requirement | Acceptance test |
|---|---|---|---|
| R49 | **M** | **NET EUR/L, final (VAT excluded, rebates applied) is THE price basis, stated on every report surface.** Effective price = `net_eur_eff / qty`. Both the **as-invoiced** (`eur_l_doc`) and **effective** (`eur_l_eff`) prices are exposed so the rebate value is visible. | Every price surface carries the basis line; every comparison/ordering uses the effective price. |
| R50 | **M** | **`net_eur_eff` carries off-invoice rebate layers** (the canonical case: a supplier invoicing at list price with a separate rebate invoice per country). **Guard the input source** so a raw file cannot silently replace an adjusted one. | Feed the un-adjusted source ⇒ the pipeline **fails or warns loudly**, it does not silently produce list-price analytics. |
| R51 | **M** | **One canonical query layer.** Every report, export, dashboard and materialized metric derives from it; nothing forks the math. Materialized metrics have a **drift check that recomputes through the same code path**, and an un-materialized period still renders via a live fallback. | Rename a canonical function ⇒ every consumer breaks; no duplicate implementation exists. |
| R52 | **S** | **Two distinct overpay definitions are preserved and LABELLED distinctly**: (a) same-day, same-country cheapest-rival; (b) country × month best-of-your-own-suppliers. They will not reconcile — **that is correct.** | Both figures appear with their grain in the label; no surface implies they should match. |
| R53 | **S** | **Legal framing per analysis must not be flattened:** contract breach = *"money the supplier owes"* (claim letter); same-day overpay = *"negotiation evidence, NOT a contractual claim-back"* (printed on every sheet); peer/excise/estimate = *"indicative, verify"*. | Each workbook carries its framing text. |
| R54 | **S** | **Anomaly detection uses NO absolute price thresholds** — every bound is learned from the data's own spread (σ-based and robust modified-z), with volume floors. | Double every price ⇒ the same rows are flagged. |
| R55 | **S** | **Peer benchmarking suppresses a cohort below the minimum contributor count** and, under multi-tenancy, restricts the cohort **intra-tenant**. | A cell with one other contributor renders "cohort too small". |
| R56 | **S** | **FX: one convention (foreign units per 1 EUR); the rate used is the most recent on or before the transaction date; both the APPLIED and the OFFICIAL reference rate are frozen per line with a provenance flag.** No coverage ⇒ **NULL, never a fabricated pass**; `no_ref` is reported separately and never counted as a pass. | A Sunday fuelling uses Friday's rate. A currency with no reference stores `fx_source='none'`, not a pass. |

### 7.6 P1 — Platform (MUST)

| # | Pri | Requirement | Acceptance test |
|---|---|---|---|
| R57 | **M** | **Four role tiers** with a **sysadmin-configurable permission matrix** for the lower three; sysadmin is never configurable; user-management and server-setup capabilities are never grantable downward. An **unset matrix cell falls back to a documented default**. | A processor granted no capability sees only open read-only pages. |
| R58 | **M** | **Every endpoint is classified into exactly one authorization set, enforced by a boot-time coverage assertion that can FAIL THE BOOT** in CI. Default posture must not be silent-allow for an unclassified route. | Add a route without classifying it ⇒ CI fails. |
| R59 | **M** | **Login by email** (unique per account, required for new accounts) mapping to an internal principal that stays the audit actor and display name. | Two accounts cannot share an email; the audit log shows the principal, not the email. |
| R60 | **M** | **Automated capture and the monthly close run OUT-OF-BAND on a worker tier, never inline in a web request.** Statement registration is enqueued, not written in-request. | No web request holds a writable product-data handle. |
| R61 | **M** | **Per-supplier rate limit / concurrency cap / backoff / circuit breaker on portal fetch, OPT-IN** — an ungoverned supplier behaves as before. **A limiter fault must degrade to "no blocking", never stop intake.** | Break the limiter table ⇒ intake continues. |
| R62 | **M** | **Retry with exponential backoff and a bounded DLQ**, with **AI-quota deferrals counted SEPARATELY from hard failures** (a quota outage is not the document's fault). DLQ + oldest-pending SLO are alerted. | A quota error does not consume a hard attempt. |
| R63 | **M** | **Multi-tenancy OFF by default is byte-identical to single-tenant**; scoping helpers are literal no-ops while off and **FAIL CLOSED** when on with no tenant bound. Writes always require a concrete tenant. | With the switch off, every generated SQL string is unchanged. With it on and no tenant, a query returns nothing rather than everything. |
| R64 | **M** | **Cross-tenant isolation test bar:** for every tenant-scoped table reachable by any route, seed A and B with overlapping data, bind A, run the REAL query path, assert A present / B absent, then mirror. **A leak in CI is a release blocker.** | The test suite contains such a test per scoped table. |
| R65 | **M** | **Handled failures go to BOTH an ops log and an admin-facing error log**; a global handler catches unhandled exceptions; real HTTP 4xx/redirects pass through un-logged. **Never swallow an exception silently.** | `grep` for a bare `except: pass` returns nothing. |
| R66 | **M** | **Schema migrations are versioned and append-only** — each statement runs once per database, positions are stable. | Re-running startup twice applies nothing new. |
| R67 | **M** | **All rendered output is escaped**; strict CSP (`script-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`); global CSRF on POST; documents served inert (attachment + nosniff); **never execute an upload**. | An XSS payload in a supplier name renders as text on every page it appears. |
| R68 | **M** | **Formula-injection safety on every CSV/Excel export** of free text. | A cell beginning `=` is neutralised in all exports. |
| R69 | **S** | **Choose the database for the target scale up front.** If multi-client SaaS is in scope, build on Postgres from day one — **every module must route through one connection abstraction** so the engine choice is real, not nominal. | A single config flag genuinely moves every module. |
| R70 | **S** | **Server-side session revocation** — disabling a user, changing their role, or reassigning their tenant must invalidate live sessions. | Disable a logged-in user ⇒ their next request is rejected. |
| R71 | **S** | **Stronger authentication:** password policy beyond minimum length; TOTP/WebAuthn as a second factor (email OTP alone is weak when the same mailbox is the login identifier). | — |
| R72 | **S** | **Sandbox the parse/OCR worker** — unprivileged, no network egress, read-only FS, CPU/memory/time limits. Pin and monitor parser libraries. | Documented and provisioned in the deployment. |
| R73 | **C** | **Track the statutory decision ladder (4/6/8 months) and interest owed on late refunds** (Arts. 19–22, 26–27) — this is **recoverable money not currently captured**. | A claim past its decision deadline surfaces the interest entitlement. |
| R74 | **C** | **Art. 9(2) sub-codes** where the refund state requires them — truck diesel should emit **`1.1.2`** (mass > 3,500 kg, diesel), not the bare top-level `1`. | Per-country configuration selects top-level vs sub-code. |
| R75 | **C** | **Home-state pro-rata / deductible-proportion (Art. 8(2)(g), Arts. 6/13)** — required if any claimant has exempt income. Today the 100%-deduction assumption is implicit; **record it explicitly.** | The assumption is asserted per entity and surfaced on the claim. |
| R76 | **C** | **Peppol Access Point inbound** and per-country SAF-T profiles (LT i.SAF-T, PL JPK, PT SAF-T) — the ViDA-driven capture path that structurally replaces portal scraping. | An AP webhook delivers into the same enqueue choke point. |

---

## 8. WHAT IS DEAD WEIGHT — do NOT carry forward

### 8.1 Architecture that must not be reproduced
1. **A 21,476-line `app.py` with 206 routes and hand-rolled HTML string concatenation.**
   ~30% of the file is HTML inside string literals; another ~34% is other strings (SQL, JS, CSS,
   docstrings); only ~35% is Python logic. There is **exactly one Jinja template in the whole codebase**
   (the base chrome in `webcore.py`); every page body is concatenated by hand. **Rebuild with a real
   template layer (or a proper SPA/API split) and route modules.** The repo has already started this
   (`routes/analytics_api.py` is the only blueprint extracted) — finish it, don't inherit it.
2. **Ninety-plus DEDICATED SQLite files.** `backup.DATA` lists **26 runtime databases** plus a long tail
   of app-owned ones. The "one DB per module" pattern was a reasonable isolation heuristic at laptop
   scale, but it multiplies migration, backup, tenancy re-key and transaction-boundary work by ~26.
   **Keep exactly two write domains** — the engine-owned product store and the app/claims store — plus
   a security/identity store, in one Postgres instance with schemas.
3. **The nominal `db.py` SQLite/Postgres abstraction.** Modules call `sqlite3.connect(DB)` directly, so
   `DB_ENGINE=postgres` moves **no module**. This is worse than no abstraction because it reads as done.
   **Decide the engine up front and route everything through one accessor.**
4. **The duplicated positional row schema.** The canonical transaction field list is declared verbatim in
   two modules and defaulted again in a third; rows are **positional lists** and every consumer hard-codes
   indices. **One typed schema object.**
5. **The overloaded `note` column** — it carries an invoice number, a rebate explanation, *and* a
   cash-at-pump flag, and it is simultaneously the field invoice resolution matches on. **Split it** into
   `invoice_ref`, `provenance_note` and typed flags. Most of the `_resolve_inv` heuristic complexity and
   the entire `note_invoice_overrides` admin table exist to compensate for this one design choice.
6. **`transactions` with no primary key and no unique constraint.** Idempotency is DELETE-by-period only.
   **Add a natural key** (supplier, invoice line identity) so duplicate suppression is structural.

### 8.2 Features that are scaffolding, not product
7. **Portal scraping has ZERO real supplier adapters.** The registry is empty; the only usable adapter is
   an offline demo fixture. The rate limiter, circuit breaker, credential custody, scheduler and worker
   lanes are all built around a capability that has never fetched a real invoice. The automation ROI story
   is currently unbacked. **Decide first whether to build it at all** — ViDA-mandated e-invoicing inbound
   (2026-2030) structurally replaces it, and the ToS/credential-sharing exposure is real. If kept, treat
   it as **explicitly transitional with a sunset date.**
8. **`finance.py` (embedded finance)** — ~36 KB of provider interface, offer modelling and an advances
   ledger behind a `NullProvider` that funds nothing. It is a genuine strategic bet, but it is **partner-
   and counsel-gated** and cannot ship without a licensed factoring partner. **Do not rebuild it before
   the partner exists**; keep the read-only `financeable()` view that reuses the recovery total.
9. **`bank_recon.py`** — same shape: an advisory reconciler with no live bank feed, pending an AISP
   partnership.
10. **`ai_assistant.py` (advisory document chat) and the MCP server.** Both are explicitly **deprioritised**
    in the project's own backlog ("DEPRIORITISED for now: AI chat, broad expense management, public
    benchmark, own finance licence"). The MCP server additionally requires an optional SDK. **Nice-to-have,
    not core.**
11. **`workflow.py` (~40 KB) + the visual workflow builder.** A configurable, immutably-versioned,
    run-pinned approval engine that, by its own design statement, **"changes NOTHING about a claim."** For
    five entities and a handful of operators this is a large surface with no legal authority. **Replace
    with a simple task list** unless a specific client demands configurable routing.
12. **The full DMS + secure-sharing suite** — `sharing.py` (53 KB), `metadata.py` (32 KB), `retention.py`
    (26 KB), `esign.py` (26 KB), `search.py`, `versioning.py`, `classify.py`, `share_watermark.py`,
    `dokobit.py`. This is a **second product** (a Papermark/DocSend/Paperless-ngx competitor) bolted onto a
    VAT-recovery engine. Data rooms, Q&A modules, page-by-page dwell analytics and NDA gates have no role
    in filing a VAT claim. **Rebuild only what the claim workflow needs:** the vault, SHA-256 dedup,
    integrity verification, retention + legal hold, and simple expiring share links. Buy or defer the rest.
13. **`invoicing.py` at 235 KB — the second-largest module in the repo.** A complete general-purpose sales-
    invoicing product (multi-company registry, four numbering series, credit notes, proforma, quotes,
    recurring invoices, CAMT.053 bank import, AR aging, four reports each in three formats, Peppol BIS 3.0
    XML, Factur-X embedding, a dependency-free PDF writer with an embedded font). **The VAT-recovery
    business needs exactly one thing from this: issuing our own service-fee invoice** — which
    `invoice_issue.py` already does in 17 KB. Everything else is a separate SKU. **Buy an invoicing
    product or make it a genuinely separate service.**
14. **`translations_lv.py`** — ~330 translated strings against a ~21,000-line UI. A Latvian user sees a
    heavily mixed EN/LV interface, and documents, reports and Excel exports aren't covered at all. **Either
    do i18n properly with extraction tooling, or drop it and ship English.**
15. **`autopilot.py`** — auto-files documents with no human review. The gate is conservative and reuses the
    real validation path, but three residual gaps matter: an *absent* verification result still passes; the
    confidence signal is producer-supplied; and there is no per-supplier or per-euro restriction on what may
    be auto-filed. **Do not rebuild until capture accuracy is measured in production.**
16. **Multi-tenancy phase-1 plumbing shipped for a single-tenant deployment.** `tenancy.py` is 22 KB, plus
    `tenant_id` columns and PK re-keys across ~25 modules and **~30 dedicated test files**, all inert
    because the switch is off — and it still cannot be turned on (the activation gate requires Postgres RLS
    and a posture programme that hasn't started). **Decide single- vs multi-tenant BEFORE writing a line.**
17. **Demo/seed data hard-coded in production modules.** Real client names, VAT numbers, addresses, bank
    references and invoice numbers are embedded as module-level constants (`customer_master.CUSTOMERS`,
    `BANKS`, `SUPPLIER_ACCOUNTS`; `supplier_master.SUPPLIERS`, `VAT_REGS`, `INVOICE_REG`;
    `vat_config.INVOICES`, `ISSUERS`). Three demo databases are **committed to the repo**. **All of this is
    configuration and fixture data, not code.**
18. **`month_config.py` as a hand-edited Python file naming input filenames and FX rates per month.** The
    monthly operating procedure literally begins "edit `month_config.py`." **Make the period and its inputs
    runtime parameters.**
19. **Known dead references** — `/files` is linked in the navigation but the view function has no route
    (404 for every user with import rights); `share_revoke` is classified in two permission structures but
    does not exist; `MODULES["analytics"]` names an endpoint that isn't the dashboard's actual name, so the
    Analytics off-switch doesn't cover `/analytics`; `doc_storage.py` is named in the project guide but does
    not exist. The coverage assertion only catches *unclassified* endpoints, not *classified-but-
    unregistered* ones. **Assert both directions.**
20. **`cloudflare.py`, `start.py`/`start.bat`/`start.command`/`start.sh`, `install_service_windows.ps1`,
    `make_cert.py`, `tls.py`** — a substantial one-click-desktop-install and self-managed-TLS surface. If the
    rebuild targets managed hosting, **all of this is deployment configuration**, not application code.

### 8.3 Things that LOOK like dead weight but are NOT — keep them
- The apparently redundant `_synthetic` / `_waivable_missing` / `_resolve_inv` centralization: it exists
  **precisely so the gates cannot drift apart.** Keep the single-predicate discipline.
- `money.py`: 60 lines that prevent a class of accounting bug. Keep exactly as-is.
- `vat_config.py` / `vat_entitlement.py`: small files carrying the most expensive knowledge in the repo.
- `capture_checks.py` and the "fail toward not crying wolf" posture on uncheckable data.
- The audit-snapshot (supplier-RED / client-BLUE highlighted PDF duplicate): a cheap, distinctive
  compliance artifact that costs ~7 KB.
- The explicit fail-open vs fail-closed decision **documented at every gate**. That discipline is the most
  valuable non-obvious asset in the codebase after the VAT rules.

---

## 9. OPEN QUESTIONS a rebuild team must have answered

### 9.1 Business model & scope
1. **Single-tenant per client, or multi-client SaaS?** This single answer determines the database engine,
   the entire tenancy design, the security programme (SOC 2 / ISO 27001), and whether items 7, 12, 13 and
   16 in §8 are built at all. **Answer this before any code.**
2. **Is the operator an agency filing on behalf of clients, a SaaS the client operates itself, or both?**
   Today the product is both at once — which is why `user` (client) and `admin` (agency) roles coexist,
   and why there are two invoicing engines. Pick one primary posture.
3. **Which of the four packages (Analytics / Invoicing / Documents / Tax refund) is actually sold?** If
   Tax refund is the product and it costs €0/month, the other three may be entirely optional.
4. **Is the subscription real?** There is no billing integration, payment processor, dunning, proration or
   tax handling. Is the subscription revenue model going live, or is the contingency fee the whole business?
5. **Which geography?** The claimants are Baltic (EE/LV/LT); the refund countries span the EU; the sales-
   invoicing module is Latvia-specific (LV VAT presets, LV €150 simplified ceiling, Latvian PDF fonts). Is
   LV specificity intentional or accidental?
6. **How many clients, entities and documents per month at year 1 / year 3?** The current design is sized
   for 5 entities and ~100 invoices/day.

### 9.2 VAT & legal
7. **Do any claimant entities have exempt income?** If yes, the **home-state pro-rata (Arts. 6/13)** must be
   modelled and the deductible proportion reported (Art. 8(2)(g)). Today 100% deduction is assumed
   implicitly and nowhere recorded.
8. **Which refund states require Art. 9(2) sub-codes?** Truck diesel should be `1.1.2` where required. The
   system emits only top-level `1`. The EC publishes the list — has anyone checked it against the actual
   refund countries in use?
9. **Non-fuel deductibility per refund country.** Diesel is safe everywhere; tolls, AdBlue, parking and
   services vary. Should the model become a per-(refund_country × expense_category) table **defaulting to
   "needs confirmation" rather than "refundable"** (as `docs/MANUAL.md` recommends)?
10. **Per-card-issuer entitlement.** Which issuers' schemes qualify as a supply of fuel (2008/9/EC) vs a
    financial service (*Vega International*)? DKV and E100 are flagged as running their own net-invoicing.
    **This must be verified per contract before filing** — is there a per-supplier register of that verdict?
11. **Should interest on late refunds (Arts. 26–27) be tracked?** It is recoverable money the system
    currently leaves on the table.
12. **Are the national-currency minimums current?** `NATIONAL_MINIMUMS` covers only Sweden and Denmark and
    the code says *"VERIFY against current national law before relying on these."* Which other non-euro
    refund states are in scope?
13. **The excise rates are a single €30/1,000 L placeholder for all seven countries.** Who owns the real
    per-country statutory rates, and how often do they change (quarterly, per the research)?
14. **Who confirms eligibility for excise** (vehicle ≥ 7.5 t, carrier registration)? It is deliberately not
    modelled.

### 9.3 Data & operations
15. **Does an authoritative supplier-file source exist per supplier, or is the "adjusted workbook" step
    manual?** The Q8 off-invoice rebate layer depends entirely on pointing at an adjusted file, with **no
    assertion that it is the right one**. How is that adjustment produced today?
16. **Is `expected` (the per-supplier tie-out target typed from the invoice PDF) maintained monthly by a
    human?** If so, that is the real onboarding and operating cost of every supplier.
17. **What is the actual capture accuracy per supplier and field?** `capture_confidence.py` exists to
    learn it, but the answer determines whether autopilot, the AI capture pipeline, or a vendor IDP
    fallback is justified at all.
18. **Vehicle identity is a supplier-specific composite string** (`card`, `card/plate`, plate, vehicle id)
    and is not normalised. Is per-vehicle analytics a real requirement? If yes, a vehicle master is needed.
19. **What is the retention decision?** 10 years is the conservative default; the actual obligation varies
    by member state and by document class. Who signs off the schedule?
20. **What is the backup / restore RPO and RTO?** Auto-backup **defaults to off**; the documented control is
    a quarterly restore drill.

### 9.4 Security & compliance programme
21. **Is evidentiary-grade audit required?** If SOC 2 / ISO 27001 or litigation-grade evidence is the
    target, the snapshot-only tamper-evidence model is insufficient and a hash chain (or WORM sink) must be
    designed in, not retrofitted.
22. **Who holds the KEK?** The default derives it from an on-disk file, so filesystem access yields every
    stored portal password. Is KMS/HSM with per-tenant BYOK a launch requirement or a later phase?
23. **Is portal scraping legally sanctioned by any supplier?** Has any client's fuel-card contract been
    reviewed for credential-sharing and automated-access clauses? Is there a consent artifact?
24. **What is the antitrust position on the operator's cross-tenant analytics scope?** The code grants an
    unfiltered cross-tenant read with **no code-enforced de-identification** — only a documented policy.
    Has counsel reviewed it?
25. **Is AV fail-open acceptable in production?** The default install blocks only the EICAR test string.
26. **Will the parse/OCR worker actually be sandboxed?** It is the single highest-value control and is
    currently documentation only.

### 9.5 Product decisions carried over from the existing backlog
27. Externalise the pooled benchmark (counsel-gated) — yes or no?
28. Activate refund financing via a licensed partner — which partner, which jurisdictions?
29. Build the Peppol Access Point inbound path, or contract it?
30. Buy an IDP vendor as a confidence-gated fallback for the hard unstructured tail (the in-repo
    recommendation is Klippa), or stay fully in-house?

---

## Appendix A — Route surface at a glance

211 registered URL rules. Navigation IA: **Home · Intake · Documents · Sharing · Analytics ·
VAT & Recovery · Master data · Invoicing · History · Tasks · Export · Admin.**

| Group | Representative routes |
|---|---|
| Auth/session | `/setup`, `/login`, `/login/verify`, `/logout`, `/account`, `/sso/login`, `/sso/callback`, `/lang/<lang>` |
| Home / value | `/`, `/value`, `/fees`, `/claim-status`, `/close` |
| Intake | `/extract` (+ `/confirm`, `/ai-review`, `/ai-verify`, `/ai-correct`, capture downloads), `/queue`, `/queue/review/<id>`, `/mining`, `/imports`, `/email-intake`, `/inbound/email`, `/estimate`, `/data` |
| Documents / DMS | `/documents`, `/doc/<id>` (+ `/meta`, `/versions`), `/search`, `/metadata`, `/retention`, `/retention/review`, `/invoices`, `/contracts`, `/esign*`, `/doc-requests`, `/dokobit/postback` |
| Sharing | `/share`, `/share/create`, `/s/<token>*` (public), `/rooms*`, `/r/<token>*` (public) |
| Analytics | `/analytics`, `/reports`, `/savings`, `/expenses`, `/compare`, `/transactions`, `/headtohead`, `/stations`, `/anomalies`, `/intel`, `/pricing*`, `/reliability` |
| VAT & Recovery | `/recovery-dashboard`, `/vat`, `/vat/unmatched`, `/readiness`, `/overcharges` (+ `/packet`, `/letter`), `/excise`, `/entities`, `/entitlement`, `/fx`, `/recovery`, `/receivables`, `/financing`, `/recon`, `/modules` |
| Master data | `/suppliers`, `/supplier-changes`, `/customers`, `/data`, `/doc-requests` |
| Invoicing | `/invoicing` + ~35 sub-routes (compose, issue, credit, recurring, reports, e-invoice XML, hybrid PDF, import) |
| History | `/history` |
| Tasks / Workflow | `/tasks`, `/tasks/act`, `/workflow/start`, `/workflows*`, `/workflow-builder` |
| Export | `/exports` hub + 24 `/export/*` (summary, master, history, compare, stations, overpay, expenses, accounting, **erp**, **saft**, **einvoice** (+batch), pricing, benchmark, peer, intel, **excise**, vat, readiness, fee, fees, receivables, evidence) |
| Admin | `/admin`, `/admin/confidence`, `/admin/tenants`, `/modules`, `/supplier-changes` |
| API (session) | `/api/pricing`, `/api/vat`, `/api/recovery`, `/api/periods\|benchmark\|compare\|headtohead\|entities` |
| API v1 (token) | `/api/v1/benchmark\|claim-status\|savings\|ingest\|scan-result\|customers[/<code>]` |

## Appendix B — Key constants a rebuild must carry (quick reference)

```
VAT REFUND
  MIN_QUARTER = 400.00 EUR        MIN_ANNUAL = 50.00 EUR
  NATIONAL_MINIMUMS  = {Sweden: (SEK, 4000, 500), Denmark: (DKK, 3000, 400)}
  DEADLINE           = 30 September of year+1        DEADLINE_RISK_DAYS = 60
  GOODS_CODE default = "10" (Other)  — NEVER "9" (luxuries/entertainment)
  Fuel = 1 · Road tolls = 4 · Other = 10  (truck diesel sub-code 1.1.2 where required)
  Art. 10 doc thresholds: >= EUR 1,000 general / >= EUR 250 FUEL
  Decision ladder 4 -> 6 -> 8 months; payment within 10 working days; interest owed after

MONEY
  Decimal, ROUND_HALF_UP.  q2 for thresholds, f2 for storage, dsum/fsum for totals.
  Price basis: NET EUR/L, final (VAT excluded, rebates applied) = net_eur_eff / qty

ANALYTICS TOLERANCES
  contract_audit.TOLERANCE            = 0.005 EUR/L
  pricing_intelligence.OVERCHARGE_TOL = 0.01  EUR/L
  anomaly.ANOMALY_SIGMAS = 2.0 ; robust modified-z cutoff 3.5
  PEER_MIN_CONTRIBUTORS  = 2  (antitrust suppression)
  Volume floors: 200 L (station), 100 L (vehicle), 300 L (station scorecard)
  my_prices date match tolerance = +/- 3 days
  FX deviation: 1.0% (workbook amber) / 2.0% (app flag) / 0.1pp (trend noise floor)

VALIDATION
  Engine tie-out: lines tolerance 0 (EXACT); gross_local 0.02-0.05; net/gross_eur 0.05
  Batch tie-out : |q2(sum) - q2(coversheet)| <= 0.02  (compared on Decimals)
  Regression drift: > 0.02 on net or vat

QUEUE
  LEASE_SECONDS 600 · MAX_ATTEMPTS 5 · BACKOFF 30s doubling, cap 1800s
  AI-quota deferral: every 4h, max 6 retries (~24h) then 'held' (separate from hard fails)
  DLQ_ALERT_MIN 1 · OLDEST_PENDING_SLO_HOURS 6
  Rate limiter defaults (OPT-IN): concurrency 2 · min interval 5s · breaker 5 fails / 300s

UPLOAD SECURITY
  MAX_CONTENT_LENGTH 25 MB · ZIP: 500 members / 50 MB member / 200 MB total
  Email attachments: .pdf/.xml/.zip only, 25 MB; zip uncompressed cap 200 MB

AUTH
  ROLES user < processor < admin < sysadmin
  scrypt n=2^16 (legacy 2^14, rehashed on login) · min password length 8
  Lockout 8 fails / 15 min per user (tenant-scoped) · 25 per IP
  Session 8h idle · OTP 6 digits / 10 min TTL / 5 attempts / 60s resend cooldown
  API token: 256-bit, shown once, SHA-256 stored, constant-time verify
  Contract number: <DDMMYYYY>/<NNNNNN>

INVOICING
  SIMPLIFIED_GROSS_CEILING_EUR = 150.00 (LV; Directive baseline 100)
  RETENTION_YEARS = 5 (issued invoice) · retention.DEFAULT_RETAIN_YEARS = 10 (VAT records)
  DEFAULT_PAYMENT_TERMS_DAYS = 14 · LV_VAT_RATE_PRESETS = (0.21, 0.12, 0.05, 0.0)
  Four independent series per company: INV · KR (credit) · PROF · PIED

EXCISE
  Countries: BE, FR, IT, SI, HU, ES, HR
  Default rate EUR 30.00 / 1,000 L  (PLACEHOLDER in the reported EUR 25-33 band)

PRICING (subscription, indicative)
  analytics 49 · invoicing 29 · documents 39 · tax_refund 0 (contingency instead)
  Contingency default: pricing_fee_pct 15%, pricing_fee_min (admin-editable)
```
