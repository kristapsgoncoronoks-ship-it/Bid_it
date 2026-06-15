# Future Plan — How the Software Evolves to Help & Support the Business

**Purpose.** A forward-looking, business-outcome-first plan: where the platform is now,
the value it creates, and the concrete path to turn the foundation we have built into a
growing, defensible business. This is the *business* companion to the technical docs —
`STRATEGY.md` (monetisation models), `ROADMAP.md` (phased delivery), `EVOLUTION_PLAN.md`
(costed deep-dive), `MULTI_TENANCY.md`, `SCALING.md`, `SAFT.md`. Read those for the *how*;
read this for the *why, the value, and the sequence*.

---

## 1. Where we are now — a working foundation, not a prototype

The product is already a **working, audit-ready accounting platform** for multi-supplier
fuel & toll spend: it ingests messy multi-format supplier data, validates and reconciles it,
recovers EU cross-border VAT (Dir. 2008/9/EC) with hard compliance gates, and turns the
line-item dataset into price intelligence, expense reports, and standards-based exports.

Crucially, the **seams for the next three business models are now built** — each deliberately
inert until a partner/credential/decision activates it, so we can switch them on without a
rewrite:

| Capability built | Business lever it unlocks | State today |
|---|---|---|
| Automated document capture (worker tier, per-supplier rate-limiter, envelope-encrypted credential custody, off-by-default scheduler, portal-fetch jobs) | The **data moat** — invoices arrive automatically across every fuel card | Seam live; needs real portal adapters + authorised credentials |
| Embedded-finance seam (`finance.py`) | **Cash-timing margin** — advance the VAT receivable | Computation live; needs a licensed factoring partner |
| Open-banking reconciliation (`bank_recon.py`) | **Reconciliation + pay-by-bank** trust layer | Advisory live (CSV today); needs an AISP aggregator for auto-feeds |
| Multi-tenant foundation (`tenancy.py` + plan) | **SaaS scale** — many clients on one platform | OFF by default; per-table enforcement is the documented next phase |
| Standards export (accounting-ledger CSV, programmable SAF-T core) | **Finance-dept buyer + ViDA tailwind** | CSV live; SAF-T core live, per-jurisdiction profile pending |
| Confidence-learning (`confidence.py`) | **Lower cost-to-serve** — skip redundant AI on trusted suppliers | Live (governs advisory AI only) |

**What this means for the business:** we are past the "can it work" risk. The remaining work
is *activation and scale*, not invention — which is a far cheaper and more predictable place
to be.

---

## 2. The business thesis — five outcomes the software exists to deliver

Everything ladders up to five measurable outcomes. Each new build is judged by whether it
moves one of these.

| Outcome | The customer's question | How the software answers it | KPI |
|---|---|---|---|
| **Recover more cash, faster** | Are we owed VAT/excise, and is every claim airtight before the deadline? | Multi-card recovery engine + hard compliance gates + deadline alerts (miss = 0) | € recovered / € claimable · days-to-refund |
| **Remove manual work** | How many hours per close; how much re-keying & invoice chasing? | Automated capture + one-click close + exception-only review | hours/close · % auto-captured |
| **Cut spend** | Are we paying a competitive net price; where are we overcharged? | Price intelligence + supplier price-review packet | € overcharges identified → recovered |
| **De-risk compliance** | If audited tomorrow, can we prove every number? | Audit trail + evidence packs + SAF-T/ledger export + provable reconciliation | audit-ready in minutes |
| **Finance the timing** | Can we get the refund cash now, not in 6–18 months? | Embedded finance over a known, line-item-underwritten receivable | financing origination € · margin |

The **moat** under all five is the proprietary, multi-network, line-item invoice dataset —
which the automated-capture build is designed to compound.

---

## 3. The evolution path — three horizons, business-outcome driven

### Horizon 1 — Activate what's built (next 1–2 quarters): turn seams into revenue

*The cheapest growth available: switch on capabilities already paid for.*

1. **Light up automated capture for the first real suppliers.** Pick the 2–3 highest-volume
   supplier portals/APIs the client is authorised to access; write their adapters; store
   credentials under the envelope-encrypted custody (KMS/BYOK); run scheduled pulls on the
   worker tier with the rate-limiter. **Business value:** manual collection disappears, the
   dataset compounds, and every later model gets richer inputs. *Gate: which portals + the
   production KMS backend.*
2. **Sign one embedded-finance partner** (Factris-style: our UI, their licence). The receivable
   is a high-certainty, line-item-underwritten asset — we monetise origination/margin with no
   licence. **Business value:** the highest-margin layer, and a hard differentiator vs. anyone
   who only files claims. *Gate: partner selection + per-Member-State counsel.*
3. **Connect one open-banking aggregator** (Tink/TrueLayer/Yapily, as agent) to auto-feed the
   reconciliation already shipped. **Business value:** "did the refund land / did we pay the
   supplier" answered automatically; sets up pay-by-bank later. *Gate: aggregator + PSD2 agent
   onboarding.*
4. **Package & price the recovery wedge.** Contingency % of recovered VAT + a thin per-vehicle
   SaaS fee — the multi-card edge captive card schemes can't match.

**Milestone for Horizon 1 ("make it worth it"): first external client live on automated
capture + one financing deal originated.** That is the proof the business model works end-to-end.

### Horizon 2 — Scale & widen the buyer (quarters 3–6): from tool to platform

*Grow accounts, widen who buys, and turn the single deployment into a SaaS.*

5. **Multi-client SaaS go-live.** Execute `MULTI_TENANCY.md` phases P1→P3: add `tenant_id` to
   each product table, wire tenant-scoped enforcement (app-level + Postgres RLS) with a
   cross-tenant access test per table, per-tenant encryption keys (the EnvKEK BYOK seam is
   ready). **Business value:** one platform serves many fleets/agencies — the economics that
   make this a *company*, not a service. *A cross-tenant leak is a GDPR breach — isolation must
   be provable, hence the test-per-table discipline.*
6. **Finance-department product: expense reports + ERP/SAF-T export.** The accounting-ledger
   CSV ships today; specialise the SAF-T core per jurisdiction (LT, PL/JPK, PT…) and add
   Xero/QuickBooks/DATEV connectors. **Business value:** widens the buyer from "VAT recovery"
   to the whole finance team, and rides the mandatory ViDA + national e-invoicing tailwind
   (2026–2030) — a regulatory wind at our back, not a headwind.
7. **Validated Postgres cutover** for multi-client volume (the dialect shim and `SCALING.md`
   are staged). **Business value:** removes the scaling ceiling before it bites.
8. **Overcharge → recovery workflow.** Turn the price-review packet into a supplier-credit
   recovery flow. **Business value:** a second, recurring € line beyond VAT.

### Horizon 3 — Compound the advantage (quarters 7+): intelligence & expansion

9. **Pooled benchmark (externalised, counsel-gated).** Anonymised cross-fleet price/markup
   intelligence via an independent-trustee model. **Business value:** a data product only we can
   sell, because only we have the multi-network dataset. *Gate: competition-law counsel.*
10. **AI copilot over the audited data** (grounded, never hallucinating a figure) + predictive
    refund/cash-flow forecasting + driver/ops mobile surface (nearest cheap station). **Business
    value:** moves the product from record-keeping to decision support — stickier, higher ARPU.
11. **Geographic & vertical expansion.** The same engine serves any cross-border fleet spend
    (logistics, bus, construction) and any EU member state once the per-jurisdiction profiles
    exist. **Business value:** TAM expansion on a built engine.
12. **Security posture as a sales asset.** SOC 2 Type II + ISO 27001/27017/27018. **Business
    value:** unlocks enterprise & agency white-label deals that won't buy without it.

---

## 4. How it supports the business, by stakeholder

- **Fleet finance / controller** — recovered cash, audit-ready evidence, one-click close,
  expense & ERP export, financed cash-timing. *The economic buyer.*
- **Fleet operations** — automated capture removes invoice chasing; price intelligence steers
  fuelling to cheaper suppliers/stations. *The daily user and overcharge-saver.*
- **The platform operator (us / an agency)** — multi-tenant SaaS, contingency + SaaS + financing
  margin, a compounding data moat, and a regulatory tailwind. *The growth engine.*
- **The regulated partners** (factor, AISP) — we bring the underwritable receivable and the
  reconciled bank view; they bring the licence. *Symbiosis without us carrying a licence.*

**Monetisation stack (priority order):** direct-to-fleet recovery (contingency + thin SaaS) →
embedded finance (origination margin) → expense/ERP/SAF-T export (seat expansion) →
open-banking pay-by-bank (transaction margin) → pooled benchmark (data product). Each layer
sells to the *same* installed base, raising ARPU without new acquisition cost.

---

## 5. Sequencing principles (so evolution stays disciplined)

1. **Capture before monetise** — the self-feeding dataset unlocks every revenue layer and the moat.
2. **Activate before invent** — switch on built seams (finance, banking, capture, tenancy) before
   building anything new.
3. **Partner, don't self-licence** — finance and open banking ride a regulated partner; we build
   the seam, they carry the licence.
4. **Money & risk beat polish** — recover cash, catch overcharges, prevent a deadline miss first.
5. **Provable isolation & compliance** — multi-tenant enforcement is gated on a cross-tenant test
   per table; every surface keeps NET-EUR final prices, `money.py` quantisation, escaped output,
   audited changes, envelope-encrypted credentials. Evolution must not erode the compliance backbone.
6. **Measure the outcome** — judge each step against the five KPIs in §2, revisited each quarter.

---

## 6. The decisions that gate the next moves (what we need from the business)

These are the *only* things standing between the built seams and live revenue:

1. **Automated capture:** which supplier portals/APIs are authorised first, who holds the
   credentials, and the production KMS/secrets backend (cloud KMS vs. Vault vs. the local key).
2. **Embedded finance:** which licensed factoring partner, and per-Member-State legal sign-off.
3. **Open banking:** which AISP aggregator, and PSD2 agent onboarding.
4. **Multi-tenant:** commit to multi-client SaaS now (triggering P1–P3) vs. stay per-deployment.
5. **SAF-T:** the first target jurisdiction (drives the country profile).
6. **Security investment:** timing of the SOC 2 / ISO programme (gates enterprise/white-label).

Each is a *business decision*, not an engineering unknown — the engineering is staged and waiting.

---

## 7. The one-paragraph version

We have built an audit-ready VAT-recovery and fuel-intelligence engine **and** the seams for
the three businesses that grow on top of it — automated multi-network capture (the moat),
embedded finance (the margin), and multi-tenant SaaS (the scale) — each ready to switch on.
The plan is therefore not "what to build" but "what to activate, in what order, with which
partner": light up capture and one financing deal to prove the model (Horizon 1), turn the
single deployment into a multi-client, finance-department SaaS riding the ViDA tailwind
(Horizon 2), then compound the data advantage with a benchmark product, an AI copilot, and new
markets (Horizon 3). The moat is the proprietary line-item dataset; the discipline is
capture-before-monetise, partner-don't-self-licence, and provable compliance at every step.
