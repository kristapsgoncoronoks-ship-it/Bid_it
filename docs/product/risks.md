# InvoiceIQ — Assumption & Risk Register

> Companion to the [PRD](./product-requirements.md). Assumptions we are betting on, and the risks that could break the product or the business. Scored **Impact** (1–5) × **Likelihood** (1–5) = **Score**; higher = address sooner. Owner and mitigation for each. Review at each planning cycle.

---

## 1. Assumptions we are betting on

| # | Assumption | If wrong… | Confidence | How we'll validate |
|---|---|---|---|---|
| A1 | Accountancy practices are the fastest beachhead (multi-client, high WTP, referral loop). | GTM motion + first console are mis-built. | Medium | 10–15 practice interviews; a design-partner practice. |
| A2 | The buyer will pay for *capture + VAT-correctness*, not just a receipt scanner. | Wedge too weak; commoditised. | Medium | WTP interviews; pilot conversion. |
| A3 | "Documents processed/month" is an understandable, fair value metric. | Pricing friction; churn at caps. | Medium | Pricing-page A/B; cap-behaviour cohort analysis. |
| A4 | Deterministic parsing handles the majority of real invoices; AI is the exception. | Capture cost + accuracy suffer; margins thin. | Medium-High | Measure deterministic capture rate on real docs early. |
| A5 | Staying out of money movement keeps us clear of PSD2/e-money licensing. | Regulatory exposure. | High (with counsel) | Legal review before any banking/lending feature. |
| A6 | EU-region hosting + a DPA + immutable audit satisfy early SME/accountant compliance needs. | Deals stall on security review. | Medium-High | Counsel review; security questionnaire dry-run. |
| A7 | A 10-year default retention with legal hold is a safe cross-border default. | Under/over-retention; legal risk. | Medium | Per-country legal review (Q7). |
| A8 | Issuing (built) is a strong upsell, not a distraction from the wedge. | Focus dilution. | Medium | Track attach rate vs. capture engagement. |
| A9 | The modular monolith + Postgres + worker tier scales to early enterprise without a rewrite. | Costly re-architecture mid-growth. | High | Load-test at 100k invoices/tenant; the documented bottleneck roadmap. |
| A10 | ViDA/e-invoicing mandates are a tailwind we can ride with EN-16931 + national formats over time. | Miss the compliance wave or over-invest early. | Medium | Track member-state timelines; sequence formats by demand. |

---

## 2. Risk register

### Regulatory & compliance

| ID | Risk | Impact | Likelihood | Score | Owner | Mitigation |
|---|---|---|---|---|---|---|
| R1 | **Cross-tenant data leak** (GDPR Art. 33/34 breach; existential for a fintech-adjacent SaaS). | 5 | 2 | **10** | Eng/Security | Defence-in-depth isolation (row + ORM guard) ✅; **every new table registered in tenant scope** (CI-enforced) ✅; automated cross-tenant test in CI ✅; **Postgres RLS belt-and-braces shipped** ✅ (a Postgres CI job runs the RLS enforcement tests); pen test before enterprise. |
| R2 | **Retention vs. GDPR-erasure conflict** — deleting a legally-retained invoice, or failing to erase when required. | 4 | 3 | **12** | Product/Legal | **Shipped** ✅: retention + legal hold (F-G9) and GDPR erasure (F-G10) that **respects** statutory retention and *surfaces* the conflict (audit + issued invoices retained and reported, never silently deleted); per-country retention config; counsel sign-off (Q7). |
| R3 | **Data residency violation** via a non-EU AI/OCR/analytics sub-processor. | 4 | 3 | **12** | Eng/Legal | EU-region default; AI pipeline opt-in/default-off/derived-data-only; documented sub-processor list + SCCs; prefer EU-hosted models (Q9/Q10). |
| R4 | **Mis-stated compliance** (claiming SOC 2/e-invoice conformance we don't hold). | 4 | 2 | 8 | Product | Only claim what's certified; roadmap SOC 2/ISO for Enterprise; validate EN-16931 output against real receivers. |
| R5 | **Inadvertent regulated activity** if finance/banking features slip in. | 5 | 1 | 5 | Product/Legal | Hard product boundary: no money movement in v1; any such feature via licensed partner + legal gate. |

### Product & market

| ID | Risk | Impact | Likelihood | Score | Owner | Mitigation |
|---|---|---|---|---|---|---|
| R6 | **Scope sprawl** — building all of A–J (AP+AR+expenses+finance+API) dilutes focus, nothing is best-in-class. | 5 | 4 | **20** | Product | This PRD's cuts; MoSCoW discipline; freeze expenses/finance/portal-scrape; ship the wedge first. |
| R7 | **Capture accuracy below trust threshold** — users don't trust drafts, keep manual entry. | 5 | 3 | **15** | Eng/ML | Deterministic-first; measure correction rate; human-in-the-loop; iterate parsers on real docs. |
| R8 | **Commoditisation** by Dext/AutoEntry/accounting-native capture. | 4 | 3 | 12 | Product | Differentiate on EU VAT/multi-entity correctness + dimensions + e-invoice in/out; own the accountant relationship. |
| R9 | **Weak activation** — users upload once and never reconcile. | 4 | 3 | 12 | Product/Growth | Onboarding to FRV; nudge the monthly close loop; surface "value found" moments. |
| R10 | **Accountant motion mis-fit** — multi-client console/pricing wrong. | 4 | 3 | 12 | Product | Design-partner practice; validate console + per-seat economics before broad build. |
| R11 | **Country/format fragmentation** — each market needs different VAT rules, e-invoice formats, retention, language. | 4 | 4 | **16** | Product/Eng | Pick 2–3 countries first (Q2); build a format/rule matrix; sequence by demand, not ambition. |

### Technical & operational

| ID | Risk | Impact | Likelihood | Score | Owner | Mitigation |
|---|---|---|---|---|---|---|
| R12 | **Money/VAT/FX miscalculation** erodes trust and creates liability. | 5 | 2 | 10 | Eng | Decimal-only money ✅; ECB provenance ✅; sampled correctness audit; regression tests. |
| R13 | **Silent job/webhook loss** (missed recurring invoice, undelivered event). | 3 | 2 | 6 | Eng | Durable queue with retry/backoff/dead-letter ✅; **DLQ alerting + queue-lag SLO shipped** ✅ (`/health/queue` 503-when-degraded + Prometheus gauges); delivery log ✅. |
| R14 | **Malicious upload** (malware, zip-bomb, XXE, formula injection in exports). | 4 | 2 | 8 | Security | Single-choke-point scan + type validation ✅; zip caps/slip neutralised; formula-injection-safe exports; inert doc serving under strict CSP ✅; sandbox the parse worker. |
| R15 | **Scaling limits** at high per-tenant volume (dashboard latency, parse throughput). | 3 | 3 | 9 | Eng | Indexed aggregation ✅; worker lanes ✅; load-test; Postgres tuning; caching where safe. |
| R16 | **AI dependency / cost / hallucination** if capture leans on external models. | 3 | 3 | 9 | Eng | Deterministic-first; AI advisory + opt-in; DLP gate over external AI; verify against source. |
| R17 | **Vendor lock-in / provider outage** (billing, OCR, hosting). | 3 | 2 | 6 | Eng | Provider-abstracted seams; graceful degradation; documented failover. |

### Commercial & GTM

| ID | Risk | Impact | Likelihood | Score | Owner | Mitigation |
|---|---|---|---|---|---|---|
| R18 | **Billing/EU-VAT complexity on our own subscriptions** slows monetisation. | 3 | 3 | 9 | Ops | Merchant-of-record (Paddle) for launch; revisit Stripe usage-billing at scale (Q12). |
| R19 | **Pricing mismatch** — value metric confuses buyers; caps feel punitive. | 3 | 3 | 9 | Product | Soft caps + visible usage ✅; A/B framing; overage without data loss. |
| R20 | **Long enterprise sales cycle** blocked on SSO/SOC 2/DPA we lack. | 3 | 3 | 9 | Product | Don't chase enterprise pre-PMF; land SME/accountants; build Enterprise trust pack deliberately. |

---

## 3. Top risks to address first (by score)

1. **R6 — Scope sprawl (20).** The single biggest threat is building everything. Enforce the wedge.
2. **R11 — Country/format fragmentation (16).** Pick 2–3 markets; matrix the rest.
3. **R7 — Capture accuracy vs. trust (15).** Measure and iterate on real documents from week one.
4. **R2 / R3 — Retention-vs-erasure & residency (12 each).** Get counsel sign-off before the first paying customer.
5. **R8/R9/R10/R12 (10–12).** Differentiation, activation, accountant fit, money correctness.

---

## 4. Open unknowns (feed the clarifying questions in PRD §12)

- Beachhead: accountants vs. direct SME (A1 / PRD Q1).
- First countries & their VAT/e-invoice/retention specifics (R11 / PRD Q2).
- Retention default + erasure policy, confirmed with counsel (R2 / PRD Q7).
- Data-residency commitment + sub-processor/AI stance (R3 / PRD Q8–Q10).
- Billing metric + provider (R18/R19 / PRD Q11–Q12).
- Real-world deterministic capture rate (A4/R7) — the number that most changes unit economics.

---

## 5. Risk summary

The business risks (**scope sprawl**, **country fragmentation**, **capture trust**) outrank the technical ones — the platform foundations (isolation, audit, durable jobs, money precision) are already strong. The compliance risks (**retention/erasure**, **residency**) are the ones that can *block a sale* and must be resolved with counsel before GA, not after. Discipline over ambition is the dominant mitigation.
