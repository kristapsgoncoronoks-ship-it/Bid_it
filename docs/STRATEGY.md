# Product & Monetisation Strategy

Strategic direction for the Fleet Fuel & VAT Refund System — what the platform is, where the
durable value and money are, and the prioritised sequence to get there. Pan-EU scope.

Synthesised from a multi-source, fact-checked research pass (June 2026). Soft figures are
flagged; market sizes are directional (definitions diverge 10–100×); provider fees are
essentially never published — model them, don't benchmark to a public number. Statutory facts
and the existence of live competitors are the hard ground.

---

## 0. Thesis (one line)

> **Turn a transport company's messy, multi-supplier fuel/toll spend into recovered cash and an
> audit-ready financial record — across every fuel card, automatically — and own the cash-timing
> of the refund by financing it.**

The defensible profit centre is **embedded finance on a card-agnostic recovery + intelligence
engine**. The moat is the **structured, multi-network, line-item invoice dataset** the platform
accumulates — which captive card schemes can't assemble (they see only their own network) and
manual VAT agencies never build.

---

## 1. The core problem (quantified)

Cross-border EU VAT/excise on fuel and tolls is large, slow, deadline-bombed, and routinely
left unclaimed:

- **Large** — ~1M EU road-transport firms, ~80% SMEs; fuel ≈ ⅓ of opex; diesel VAT 17%→27%
  (LU→HU). *Illustrative model (no authoritative per-fleet figure exists):* a 10-truck fleet
  buying ~100,000 L/quarter abroad ≈ **~€28k VAT/quarter (~€110k/yr)** recoverable on foreign
  fuel alone, before toll VAT and diesel-excise rebates (~€0.19/L BE; ~7–9 states).
- **Slow** — the refund state has 4 months to decide (→6→8 with queries) + 10 working days to
  pay (Dir. 2008/9/EC). Multi-month cash lock-up.
- **Deadline-bombed** — miss **30 Sept of year+1** and the right is permanently lost;
  sub-threshold quarters (€400 quarterly / €50 annual) get stranded if not mopped into the
  annual claim.
- **Left on the table** — industry sources cite >€2bn unclaimed foreign VAT/yr and ~21% of
  businesses recovering none (both industry-sourced, directional). Cause: 27 jurisdictions,
  per-country powers of attorney, FX, missing/non-compliant invoices, missed deadlines.

**The platform already embodies the hard parts:** deterministic Factur-X/structured-invoice
parsing, the lock/threshold/deadline gates, per-country PoA generation, the receivable data
layer, and independent per-invoice ECB verification.

**Sharpest value proposition:** *"Recover every euro of fuel & toll VAT and diesel excise you're
owed across all your fuel cards — filed correctly and on time, with the cash advanced to you in
days instead of months — and get a clean, audit-ready financial record for free."*

---

## 2. Capability bets (what to build next, in order)

### A) Expense reports + accounting/e-invoicing export — ride the ViDA mandate
Generic expense tools rebuild data from photographed receipts via OCR; the platform already
holds **line-item fuel/toll/AdBlue transactions per vehicle/driver**, so expense reports,
mileage/per-diem, fraud signals and accounting export fall out with **no capture step**.
- Market: expense-management software ~$7.6bn global (2024), ~10% CAGR; incumbents price
  ~€9–50/user/mo; **accounting integrations (Xero, QuickBooks, DATEV, Sage, NetSuite) are
  table-stakes**.
- **The tailwind that matters most:** ViDA adopted Mar-2025; mandatory EN-16931 e-invoicing +
  digital reporting for intra-EU B2B by **1 Jul 2030**, with national mandates landing
  **2026–2028** (Poland KSeF, Belgium, France, Germany); SAF-T spreading. Structured invoice
  data — exactly what the engine parses — becomes universal and mandatory.
- **Action:** build SAF-T / e-invoice / ERP export. Future-proofs ingestion and widens the
  buyer from "transport ops" to "the finance department."
- *Caveat:* the CSRD/CO2 "ESG tailwind" was **cut** by the Apr-2025 Omnibus — keep CO2 reporting
  a cheap feature, not a go-to-market thesis.

### B) Open banking — partner, don't licence
- Use for: statement aggregation (reconcile fuel-card statements ↔ bank payments — the engine
  already reconciles), payment initiation (pay suppliers / remit refunds / disburse financing
  at <1% vs >2.5% card), cash-flow forecasting.
- **Licensing:** do NOT get your own AISP/PISP/EMI licence. Become an **agent of a licensed
  provider / consume an aggregator's rails** — partner carries PSD2 liability; agent onboarding
  ~weeks vs many months for direct authorisation.
- **Aggregator shortlist:** Tink (Visa), TrueLayer (payments), Yapily (API-first/white-label —
  best for embedding). ⚠️ GoCardless/Nordigen Bank Account Data **closed to new signups Jul-2025**.
- **Regulatory direction:** PSD2 today; **PSD3+PSR provisionally agreed 27 Nov 2025** (~late-2027
  compliance) makes bank APIs more reliable. **FIDA still in trilogue — don't bank on it yet.**

### C) Adjacencies on the same data
Toll-VAT recovery (in scope under 2008/9/EC), diesel-excise rebates (ETD 2003/96, ~7–9 states),
fuel-card-misuse/fraud detection (a premium add-on elsewhere), and the existing fuel-price
competitiveness intelligence.

---

## 3. The four monetisation models (pan-EU)

| Model | TAM (directional) | Pricing | Top competitors | #1 risk | Fastest first revenue |
|---|---|---|---|---|---|
| **① SaaS to VAT-refund agencies** (tooling vendor) | Niche, sticky | Per-seat + per-claim | In-house agency tools; EDICOM/Marosa | Arming competitors; few buyers | White-label the engine (API + CRM + doc-gen already built) to 2–3 friendly agencies |
| **② Direct-to-fleet self-serve** | Largest base (~1M firms, 80% SME); cross-border subset | **Contingency % of recovered VAT** (sector norm) **+ thin per-vehicle SaaS fee** | Captive: DKV (Net Invoice), UTA Edenred (~14-day or pre-finance), Eurowag; manual: VAT IT, FastVAT | Card schemes bundle reclaim as retention | Onboard fleets using **2+ card networks** (the unfair advantage) |
| **③ Data / intelligence sale** (pooled benchmark) | Smaller, premium | Subscription to anonymised reports | greenfield | **Competition law** — redistributing rivals' live prices = textbook hub-and-spoke | **Internal-only first** (shipped). Externalise only via independent-trustee/aggregated/historic model + counsel |
| **④ Embedded finance** (advance/factor the receivable) | Europe ≈60% of global factoring; advance 70–95%, fee ~1–5% | **Origination/margin share** per advance | **Proven:** FastVAT/Negométal/Vatecure pre-finance in 3–10 days; **Eurowag "Cash" = UI + Factris licence** | Factoring = regulated lending (CRD Annex I), per-country; CJEU: factoring fees VAT-taxable | Copy the Eurowag/Factris pattern: your portal + a licensed factoring partner |

---

## 4. Prioritised roadmap

1. **Win direct-to-fleet on the multi-card wedge (Model ②).** The only structural edge over
   captive card schemes and the only thing manual agencies can't match on software+speed.
   Hybrid pricing: contingency on recovered VAT (no-win-no-fee lowers buyer risk) + thin
   per-vehicle SaaS fee.
2. **Bolt on embedded finance (Model ④) — the profit centre.** "Advance the refund in days" is
   *already proven* by 3+ competitors → validated, not speculative. Partner with a licensed
   factoring provider (Factris-style); monetise origination/margin with no licence. Converts a
   fee-for-service tool into a capital-efficient fintech. A VAT refund is a high-certainty
   receivable from a tax authority — an ideal asset to advance (and the platform can underwrite
   it because it can see it line-by-line).
3. **Ship expense-reporting + SAF-T/e-invoice/ERP export (bet A)** to ride ViDA/national
   e-invoicing mandates 2026–2030; deepens stickiness, widens the buyer, future-proofs ingestion.
4. **Add open-banking reconciliation + pay-by-bank (bet B)** via an aggregator/agent partner;
   closes the statement↔payment↔refund↔financing loop and cuts payment cost.
5. **Keep the pooled benchmark internal (Model ③); externalise later** behind a trustee/
   aggregation model + counsel. **License the engine to agencies (Model ①) opportunistically** —
   found money once the platform exists; don't lead with arming competitors.

---

## 5. The moat

The **proprietary, structured, multi-network fuel/toll invoice dataset** — line-item, per-vehicle,
VAT-decomposed, FX-verified, across *every* card a fleet uses. Captive card networks each see only
their own slice; manual agencies never assemble it; telematics players (Webfleet, Geotab, Samsara,
Microlise) own vehicle data but **do no VAT or financial settlement**. That dataset compounds:
better recovery → lower-risk financing → credible benchmarks → expense/compliance automation. The
one company converging on the whole bundle — **Eurowag** (payments + Inelo telematics + Factris
finance) — is **card/network-tied**, which both validates the thesis and marks the competitor to
stay independent of.

---

## 6. Key caveats (don't over-rotate)

- Provider contingency %s are unpublished — model pricing, don't benchmark to a public number.
- Market sizes diverge 10–100× ("software revenue vs spend transacted") — directional only.
- Factoring/credit licensing is Member-State-specific (incl. across the five Baltic entities) —
  local counsel before the finance product.
- FIDA and CSRD both weakened/slipped in 2025 — no thesis should rest on either tailwind.
- Embedded finance is competitive and proven — attractive, but you're late to be first; the edge
  is the dataset + the existing recovery engine, not novelty.

---

## 7. Strategy → backlog

New buildable items derived from this strategy (added to `docs/BACKLOG.md` §C-Strategic):
- **SAF-T / e-invoice / ERP export** (expense + ViDA tailwind) — *the highest-value capability bet.*
- **Embedded-finance partner integration** (factor the VAT receivable; Factris-style) — *the profit centre.*
- **Open-banking reconciliation + pay-by-bank** (aggregator/agent partner).
- **Company expense reports** (per-vehicle/driver, mileage/per-diem) on the existing transaction data.
- **External pooled benchmark** — remains counsel-gated (already tracked).

Sources: EUR-Lex / EU Commission, Tax Foundation, Eurostat, IRU; EU competition-law guidance
(Norton Rose, Herbert Smith, Bird & Bird); PSD3/FIDA (Norton Rose, Freshfields); factoring /
embedded finance (Precedence, eCapital, Factris, Hokodo); provider/competitor pages (DKV, UTA
Edenred, Eurowag, FastVAT, Negométal, Vatecure, Pleo, Spendesk, Tink, TrueLayer, Yapily, Webfleet,
Geotab, Samsara). Full inline citations in the originating research report.
