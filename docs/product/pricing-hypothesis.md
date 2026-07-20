# InvoiceIQ — Pricing & Packaging Hypothesis

> Companion to the [PRD](./product-requirements.md). **Hypotheses to validate**, not committed prices. Grounded in the existing plan model (`backend/app/services/plans.py`: trial/starter/pro/enterprise, seat + module gating, €29/€99) but proposes a deliberate value metric and an accountant-partner motion.

---

## 1. Packaging principles

1. **Charge for value delivered, not cost incurred.** The value is *invoices turned into a clean, compliant record*. The natural value metric is **documents processed per month**.
2. **A hybrid: seats + usage.** Seats capture team size (accountants scale on people); documents capture volume (SMEs scale on invoices). Charging on both aligns price with both drivers without punishing either.
3. **Gate capabilities by plan, meter volume within plan.** Module gating already exists; add clear monthly document allowances with overage.
4. **Compliance sells the top tiers.** EU residency, DPA, retention config, SSO, audit exports are the enterprise wedge — not more features, more *trust*.
5. **A distinct partner (accountant) motion.** Multi-client management + per-seat economics, billed to the practice, not per client.
6. **No dark patterns.** Usage and limits are visible in-product (already metered); upgrades are one click; downgrades allowed.

---

## 2. Value metric & usage-metering model

**Primary billing metric:** **documents processed / month** — a *document* = a supplier invoice captured-and-confirmed **or** an uploaded file parsed. (Both are already metered: invoices off the invoices table, uploads in `usage_counters`.)

**Why this metric**
- Monotonic with value (more invoices handled = more work saved).
- Understandable ("you processed 420 invoices this month").
- Already instrumented and enforceable (per-plan limits return 402 at the cap).
- Resistant to gaming (a confirmed invoice is a real unit of work).

**Secondary / dimension metrics (for packaging & limits, not primary price):**

| Metric | Used for | Instrumented? |
|---|---|---|
| Documents processed / month | Primary allowance + overage | ✅ |
| Uploads / month | Fair-use guard on parsing/OCR cost | ✅ |
| Active seats | Per-seat line on team/partner plans | ✅ |
| Issued invoices / month | Fair-use on the issuing module | 🟡 (issued invoices countable) |
| Legal entities | Business/Enterprise gating | ✅ |
| API calls / webhook deliveries | Fair-use / Enterprise metering | 🟡 (jobs + deliveries logged) |
| Storage (document vault) | Enterprise metering | 🟡 |

**Overage policy hypothesis:** soft cap → warn at 80%/100%, allow a grace buffer, then either block new captures (Starter) or auto-charge a per-document overage (Team/Business). Never lose or delete a document because of a limit.

---

## 3. Proposed subscription plans

Prices are **hypotheses** (EUR, monthly, billed annually with ~2 months free). Positioned against Dext/AutoEntry (capture, ~€30–60/mo entry) and light AP tools, undercutting enterprise AP (Basware/Tipalti).

| Plan | Target | Price (hyp.) | Seats | Docs/mo | Entities | Key capabilities |
|---|---|---|---|---|---|---|
| **Free** | Trial / micro | €0 | 1 | 25 | 1 | Capture + review + dashboards (core). No exports beyond CSV preview. Watermark on issued PDFs. |
| **Starter** | Finance-of-one SME | €39 | 3 | 150 | 1 | Everything in Free + full CSV/Excel export, duplicate/VAT checks, email-in intake, audit log. |
| **Team** | Growing SME | €99 | 10 | 750 | 3 | Starter + issuing module (issue/credit/recurring/reminders), dimensions analytics, webhooks, API ingest, overage-per-doc. |
| **Business** | Multi-entity SME | €249 | 25 | 3,000 | 10 | Team + SAF-T/ERP export, per-country VAT, retention config + legal hold, priority support. |
| **Enterprise** | Large / regulated | Custom | Custom | Custom | Unlimited | Business + SSO/SAML/SCIM, residency guarantee, DPA + SOC 2/ISO, audit exports, SLA, sandbox, custom limits. |
| **Practice (Partner)** | Accountancy firms | €X/seat + client packs | Per-seat | Pooled across clients | Many | Multi-client console, pooled document allowance, per-client isolation, white-label option (Later), consolidated billing. |

**Notes on the mapping to the existing model**
- Current code has `trial/starter/pro/enterprise` at €0/€29/€99/custom. Proposed adds **Free**, renames **pro→Team**, inserts **Business**, and adds the **Practice** partner plan. The module-gating mechanism already supports this; only the plan table + limits + a billing provider need wiring.
- Overage requires a metered-billing integration (Stripe usage-based or Paddle).

---

## 4. Add-ons (à la carte, any plan ≥ Starter)

- **Issuing module** (if not bundled) — outbound EN-16931 invoicing.
- **Advanced export pack** — SAF-T + DATEV/Xero/QuickBooks.
- **Extra document packs** — e.g. +500 docs/mo.
- **Extra entities** — beyond plan allowance.
- **Portal-capture connectors** (Later) — per-supplier automated fetch.

---

## 5. Billing & tax considerations

- **Provider choice (open question, PRD Q12):** **Stripe** (control, usage-based) vs. **Paddle/merchant-of-record** (handles EU VAT/MOSS on *our* subscription invoices, reduces our compliance load). For an EU-selling SaaS, a merchant-of-record materially cuts VAT-registration overhead early — lean Paddle for v1, revisit at scale.
- **Currency:** price in EUR primary; GBP for UK; USD later.
- **Free-trial mechanics:** 14-day full-feature trial (maps to existing `trial` plan) with a clear activation goal (see [metrics](./metrics.md)); credit card optional to reduce friction for the accountant motion.
- **Annual incentive:** ~17% discount (2 months free) to improve cash + retention.

---

## 6. Pricing risks & things to test

| Risk / unknown | Test |
|---|---|
| Document-count metric confuses buyers who think in seats | A/B the pricing page: seats-first vs. docs-first framing; interview 10 accountants. |
| SMEs balk at usage caps ("will I get blocked mid-month?") | Soft-cap + overage messaging; measure upgrade vs. churn at cap. |
| Issuing bundled vs. add-on | Test bundle in Team vs. add-on uptake. |
| Accountant per-seat price sensitivity | Willingness-to-pay interviews; anchor on hours saved × billable rate. |
| Overage feels punitive | Compare block vs. auto-overage churn; ensure no data loss ever. |
| Enterprise "custom" too vague | Publish a starting floor to qualify leads. |

---

## 7. Pricing summary (hypothesis to validate before GA)

- **Metric:** documents processed/month (primary) + seats (secondary), both already metered.
- **Ladder:** Free → Starter €39 → Team €99 → Business €249 → Enterprise custom, plus a **Practice** partner plan for accountants (the beachhead's economics).
- **Compliance = the up-tier lever** (residency, DPA, retention, SSO).
- **Provider:** lean merchant-of-record (Paddle) for EU VAT simplicity at launch.
- **Guardrail:** never delete/lose a document due to a limit; caps are soft with visible signalling.
