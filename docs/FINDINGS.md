# Deep-research findings — logic flaws + monetization

Two-part analysis: (1) **software-logic correctness flaws** with fixes (cited to code), and
(2) a **monetization analysis** of the data-processing assets, grounded in business-model and
EU-legal research. Reliability/stall issues are covered separately in `RELIABILITY.md`.

> Headline link between the two parts: the **analytics correctness bugs degrade the very data
> product you'd monetize** — fix the math (Part 1) before selling any benchmark (Part 2).

---

## PART 1 — Software logic flaws (prioritized)

### Immediate, low-risk fixes (wrong number, tiny diff)
| Flaw | Where | Why wrong | Fix |
|---|---|---|---|
| Unknown product → goods code `9` (luxuries, never-recoverable) | `vat_refund.py:920` | Any product not in `GOODS_CODE` silently files under the one non-refundable code | Default `"10"` (Other), or tag UNMATCHED so the gate blocks |
| Falsy-zero guards drop legit `0.0` prices | `pricing_intelligence.py:247,253,255,259` | `if my`/`if pack_avg` treat a real `0.00` benchmark as "no data" → row dropped | Use `is not None` (as line 251 already does) |

### High — wrong VAT figure or wrong fee
- **Thresholds tested in EUR for non-EUR countries** (`vat_refund.py:853-864`). €400/€50 floor is legally in *national currency* for SE/DK/PL (`vat_config` documents this); EUR comparison wrongly defers valid claims. → per-currency minimum table; compare `vat_local`.
- **Threshold never gates submission** (`vat_refund.py:578-650`). A €120 quarter can be locked+filed, rejected by the refund state, and **permanently lock those invoices out of the annual mop-up.** → real block/override below-minimum.
- **`paid_amount` written nowhere** (`vat_refund.py:485-497`). Fee always falls back to *full claimed* VAT → **over-charges customers on partial refunds** (routine under 2008/9). → add a "record payment (amount,date)" action; recompute already exists.
- **Quarterly fee-freeze base wrong** (`vat_refund.py:472-477`). Sums *all* period VAT, not the actual `claim_set` (annual path is correct). → sum `invoice_lines(claim_set)` like the annual branch.

### Medium — wrong analytics / report numbers
- **Benchmark filters by `period` but buckets by `date`** (`pricing_intelligence.py:281-296`). Off-period stragglers land in the wrong bucket → corrupts the headline "best price"/"avoidable overpay"; also `supplier_grid`/`margin_report`. → bucket on the same dimension you filter.
- **"vs pack" gap un-weighted** (`pricing_intelligence.py:241`). Volume-weighted price vs *simple mean* of competitors → a 50 L outlier moves it like a 40,000 L fill. → volume-weight (`Σother_net/Σother_qty`; qty already stored).
- **MoM compares the previous *loop row*, not the prior calendar month** (`history.py:120-130`) — gap-blind.
- **Money `f2` sweep** — bare `round()` on currency in `build_master.py`, `queries.py`, `reports.py`, `contract_audit.py`, `pricing_intelligence.py` → cent drift / threshold flips vs the HALF_UP VAT basis.
- **Coversheet tie is a flat ±0.02** regardless of line count (`validate.py:96`) — a 20-line statement can tie-fail or hide a ~€0.10 error. → scale by line count or tie on un-rounded Decimal sum.

### Verified CORRECT (don't chase)
Lock lifecycle (3B/3C/3D keep; only `withdraw_claim` releases), `_synthetic` refusal, fee-freeze
is actually frozen, `internal_benchmark` overpay math, div-by-zero guards, rebate handling,
`money.py`, `contract_audit`, anomaly modified-z, ECB nearest-prior FX, and the duplicated overpay
loops (they **agree** — pure tech-debt, safe to consolidate). **Test gaps:** no coverage for
`margin_report`/`pack_avg`/`history` MoM — add tests *before* fixing the period-bucketing/pack-mean.

### Open product questions (gate the High fixes)
- Is the €400/€50 minimum a hard **gate** or only advisory? (Today: advisory.)
- Is the service fee charged on **claimed** VAT or **actually-paid** refund? (Code intends paid; never captures it.)
- Is country diesel **recoverability/pro-rata** in scope? (No logic exists anywhere.)

---

## PART 2 — Monetization analysis

**Data assets:** validated fuel transactions (5 entities × suppliers × countries), the self-sourced
NET-EUR/L price benchmark + wholesale index, VAT-recovery throughput + the service-fee engine,
contract-compliance/overpay detection, the SHA-256 document vault, the light CRM, the API-plugin seam.
These score well on the monetizability tests (uniqueness, granularity, provenance/trust) — the data is
self-sourced, NET-basis, validated, hashed, audit-logged.

### The decisive strategy: indirect first
MIT CISR's I-W-S framework + its finding that **~82% of data-monetization returns come from *improving*
operations vs ~18% from *selling* information** → **lead with indirect monetization** (use the pooled
intelligence to lift your own recovery rates and win better fuel deals, priced as savings-share/
contingency). It captures value **without selling data**, avoiding the competition-law/GDPR minefield.
The external benchmark product is a higher-risk Phase 2.

### Opportunities (prioritized)
| # | Opportunity | What's sold / to whom | Model (evidence) | Effort | Legal risk |
|---|---|---|---|---|---|
| 1 | **Plug the fee leaks** (Part 1 #3/#4) — stop losing revenue today | n/a | Contingency already your model (VAT recovery ~15–30%, PRGX 10-K) | Tiny | None |
| 2 | **Premium analytics tier** | overpay/benchmark/anomaly intelligence to your own clients | Freemium→premium (~2–8% convert); per-seat/usage | Low | Low (their own data) |
| 3 | **Pooled fuel-price benchmark** ⭐ | anonymized "you vs peer median" to fleets | Give-to-get reciprocity (SCOR) + subscription | Medium | **High — gating** |
| 4 | **API / data-plugin** | metered access to extraction/validation/benchmark via `/api` seam | Usage-based (NRR ~120%); B2B take rates low (AWS 3% vs 15% app-stores); partner rev-share | Medium | Med |
| 5 | **Embedded finance** | **finance the VAT-refund receivable** + fuel-spend; you already track claim amount+status | Transaction take-rate; top of the ladder (2–5× revenue/customer) | Large | Med (lending/licensing) |
| 6 | **VAT-recovery SaaS, productized** | the 7 delegated works as multi-tenant SaaS | Hybrid subscription + contingency; differentiator = automation + **audit-readiness (your SHA-256 doc integrity)** | Large | Med |

The natural **upsell ladder:** onboarding (subscription) → recovery (contingency) → analytics (upsell)
→ benchmarking (subscription/API) → embedded finance (take-rate) → API/plugin (usage + rev-share).
Fuel-card incumbents (DKV, UTA/Edenred) already monetize VAT/fuel-tax refund as a commission service —
this product's fee engine sits in the same space.

### Legal guardrails — what's actually sellable (Opportunity 3 is the gated one)
**The #1 risk is EU competition law, not GDPR.** A platform pooling competitors' fuel prices is a
textbook **hub-and-spoke** (the platform is the "hub"); current/granular price exchange is an Art. 101
**by-object** risk; fines up to **10% of global turnover**, the **platform operator itself liable**
(T-Mobile C-8/08; Dole C-286/13 P; Asnef-Equifax C-238/05; 2023 Horizontal Guidelines).

A defensible benchmark must therefore be:
- **historic only** (no current/forward prices — fuel moves daily, so interpret "historic" conservatively);
- **aggregated so it cannot be disaggregated** back to a contributor (Asnef-Equifax);
- **minimum cohort** ≥5 contributors, **no single >25%** of any cell, small-cell suppression, large-k
  (k=5 SDC / k=11 public-release); ⚠️ the US 5/25/3-month "safe harbor" was **withdrawn Feb 2023** —
  now best-practice, not immunity; the EU never offered a numeric safe harbor;
- run by an **independent trustee / clean team — NOT the platform feeding intelligence back** (this is
  what defuses hub-and-spoke);
- **only own-data + final aggregate returned** to each participant, never individualized competitor data.

**GDPR (parallel):** B2B is not exempt — **sole traders / drivers / cardholders make the data personal**
(common in Baltic transport). **Resale is a new purpose** (Art. 5(1)(b)/6(4)) — consent-at-pool-scale and
legitimate-interest-for-resale are both weak. The only clean route is **irreversible anonymization +
aggregation** surviving WP216's singling-out/linkability/inference tests (delete event-level source);
**pseudonymization is NOT anonymization** (EDPB 01/2025 — you hold the key → still personal data for you).

**EU Data Act (from 12 Sep 2025):** Art. 13 voids **unilaterally-imposed unfair B2B data-use clauses**
(exactly the "we may aggregate+resell your data" T&C against SME carriers); Art. 4 bars using in-scope
data to a contributor's competitive detriment (threatens the price-intelligence product if built on their
data); FRAND + portability rights. Fuel-card *transaction* data is likely **out of** the Chapter II IoT
regime, but **telematics data would be in scope.**

**Contract/ownership:** there is **no property in data** in the EU — your right to pool/resell is built
**entirely from contract** (explicit license + permitted-secondary-use + resale clause + customer
right-to-contribute warranties + a DPA where you are the **controller** for the resale purpose, since a
processor reusing data for its own purpose is reclassified as a controller, Art. 28(10)).

### Bottom line
1. **Fix Part 1 first** — the fee bugs leak money *today*; the benchmark bugs make Opportunity 3 unsellable.
2. **Opportunity 2** (premium analytics to existing clients) — lowest risk, fastest.
3. **Indirect benchmark → savings-share** (highest expected yield, avoids the legal minefield).
4. **Opportunity 3 external benchmark** only as Phase 2, behind the full guardrail stack **and EU
   competition + data-protection counsel.** Embedded finance (Opp. 5) is the highest-LTV layer.

*This is research synthesis, not legal advice. The price-pooling design in particular requires EU
competition-law and Baltic-jurisdiction counsel before launch.*
