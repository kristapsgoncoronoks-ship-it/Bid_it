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

## 7. Data-acquisition architecture (automated document capture)

The platform should feed itself — automatically capturing fuel/toll invoices & statements so the
recovery + analysis runs without manual upload. This is the flagship near-term build and the moat
engine (it's how the multi-network dataset accumulates).

**Capture both ways; lead with the structured path.** The industry (and EU law) is moving from
portal-pull to structured push: PSD2 deprecated bank screen-scraping in favour of APIs+tokens, and
ViDA + national mandates (Poland KSeF, Belgium, France, Germany — 2026-2028) make EN-16931
e-invoicing mandatory, so invoices arrive machine-readable. **But most fuel/toll suppliers in road
transport have low IT maturity** — no API, no e-invoicing — so credential-based portal scraping is a
**required first-class capability, not just a fallback.** Precedence (per capture source):
1. **Supplier API / EDI** where it exists.
2. **E-invoicing inbound** (PEPPOL/EN-16931, email/invoice-inbox) — grows automatically as ViDA lands.
3. **Credential-based portal scraping** (`portal_scraper.py` adapters) for the low-IT long tail.
4. **Manual upload** (the existing `/queue` waiting room) — always available.

**Run it out-of-band (don't overload the system).** All fetching/scraping is slow, bursty,
externally-dependent and failure-prone (MFA/CAPTCHA/timeouts/layout drift) — so it runs on the
existing durable intake queue (`waiting_room.py`: lease/retry/backoff/DLQ) on a **dedicated worker
tier** (`FFS_ROLE`, `python waiting_room.py --work` — the D6 item), never inline in a web request.
Add **per-supplier rate-limit + concurrency caps + exponential backoff + a circuit-breaker** so one
flaky/overloaded portal pauses only its own jobs (protects both your system AND keeps you under the
supplier's anti-bot radar / out of a ban). Keep orchestration in-house (the data is the moat); buy
only anti-bot infra (rotating proxies / CAPTCHA) externally if you hit walls.

**Credential custody (the "we hold client logins" security concern).** Best-practice, per OWASP/NIST:
- **Envelope encryption** (KEK wraps a per-secret DEK) backed by **KMS/HSM** (AWS KMS / Azure Key
  Vault / HashiCorp Vault) — never plaintext; the at-rest store is useless without the KMS call.
- **Per-tenant / customer-supplied keys (BYOK)** so the platform cannot bulk-decrypt one client's
  credentials — directly limits insider and bulk-breach exposure.
- **Prefer OAuth / scoped, revocable, expiring tokens over stored passwords** wherever a supplier
  supports it (the RPA "credential vault" pattern — CyberArk/UiPath — is the exact analogue).
- **Least-privilege + full audit** on every credential access; automated rotation.
- GDPR Art. 33/34 (72-hour breach notice) is the liability backdrop; target **SOC 2 Type II + ISO
  27001/27017/27018** as the platform handles credentials + financial data.

**Multi-tenant isolation (one client must never see another's data — the legal imperative).** Today
the system serves several entities within ONE deployment (per-entity model + `ADMIN_ONLY`/perms). If
it goes multi-CLIENT SaaS, tenant-isolation becomes first-class: a tenant context enforced at EVERY
query (row-level security / tenant-scoped keys), automated cross-tenant access tests, and ideally
per-tenant encryption. A cross-tenant leak is a personal-data breach (GDPR Art. 33/34, fines up to
€20m / 4% turnover) and a contract/trust failure — so isolation must be *provable*, not assumed.

## 8. Roadmap

See **`#product-roadmap`** — the phased, sequenced plan tying this strategy, the data-acquisition
build, and the open backlog into delivery horizons.

## 9. Strategy → backlog

New buildable items derived from this strategy (added to `#backlog` §C-Strategic):
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

---

## Consolidated documentation — contents

The strategy content above is unchanged. The sections below consolidate the former forward-looking docs (roadmap, evolution/costed plan, backlog, findings, multi-tenancy program, security/compliance plan) into this file.

- [Product Roadmap](#product-roadmap) — the phased delivery plan, with the costed deep-dive (build/buy, the 18-month sequence, the owner decisions) folded in.
- [Backlog](#backlog)
- [Deep-research findings — logic flaws + monetization](#deep-research-findings-logic-flaws-monetization)
- [Multi-tenancy program plan](#multi-tenancy-program-plan)
- [Security & Compliance Evolution Plan — operating as a multi-client SaaS](#security-compliance-evolution-plan-operating-as-a-multi-client-saas)

---

## Product Roadmap

**Purpose:** sequence the move from a solid internal back-office tool into a **self-feeding,
revenue-generating, pan-EU platform** — recover more VAT faster, remove manual work, de-risk
compliance, and monetise. Ties together the strategy above (the why + monetisation models) and
`#backlog` (the concrete items). Organised by **business outcome**, then phased into delivery
horizons; the **costed deep-dive** (each idea with build/effort/$/timeline, the 18-month
quarter-by-quarter sequence, the 3 first decisions, and the "make-it-worth-it" milestone) is folded
in at the end of this section. Direction, not a contract — revisit each quarter against the KPIs.

### The outcomes everything ladders up to

| Goal | Money question | KPI |
|---|---|---|
| **Recover more VAT, faster** | Are we owed VAT, and is every claim airtight before the deadline? | € recovered / € claimable · days-to-refund · % rejected · deadline misses (target 0) |
| **Remove manual work** | How many hours per close; how much re-keying / invoice chasing? | hours/close · % invoices auto-captured · exceptions only |
| **Cut fuel spend** | Are we paying a competitive net price; where are we overcharged? | € overcharges identified → recovered |
| **De-risk compliance** | If audited tomorrow, can we prove every number? | audit-ready in minutes; evidence reproducible |
| **Monetise** | Where's the recurring revenue and the financing margin? | ARR · contingency € · financing origination € |

### What's already shipped (no longer roadmap)
Monthly-close orchestrator (`engine_close.py`); notify digest + scheduler; the full
`except: pass`→`applog` migration; money-precision sweep; reliability (DLQ alerting + oldest-pending
SLO, register-failure reconcile, backup-close guard, atomic `record_payment`); the **§B
VAT-correctness suite** (national-currency threshold hard-gate + override, receipt-control
gate + waive, FX provenance + **independent per-invoice ECB verification**); the **CRM integration
API** (`/api/v1` read+write, scoped, audited); and the **full document-management module**
(prefill contracts/PoAs from CRM, `.docx`→PDF, request lifecycle, cross-module wiring); under-used
-data analytics (time-of-day, per-vehicle €/L, parser-priority, import/audit trends, reliability
scorecard).

---

### Phase 0 — Foundation for the self-feeding platform (now, in-repo, no partner)
*Prerequisites for everything else; highest ROI is removing manual babysitting.*
- **D6 — dedicated worker tier** (`FFS_ROLE=web` + `python waiting_room.py --work`). The execution
  substrate for automated fetching/scraping; isolates heavy out-of-band work from the web app.
- **Per-supplier rate-limit / concurrency / backoff / circuit-breaker** primitive on the intake
  queue — so fetching never overloads our system *or* trips a supplier's anti-bot/ban.
- **Test coverage** for `invoice_control`/`ingest`/`build_master`/`history`.
- **One-click monthly close** behind a guarded button with a live progress log.
- **Per-event notification alerts + SMTP relay UI** (digest already done).

### Phase 1 — Automated document capture (the flagship; builds the moat)
*Make the platform feed itself across every supplier — this is what accumulates the proprietary
multi-network dataset and removes the manual upload burden.* (See `docs/STRATEGY.md` §7.)
- **Build BOTH capture paths, lead with structured:** (1) supplier **API/EDI** where it exists; (2)
  **e-invoicing inbound** (PEPPOL/EN-16931, email/invoice-inbox) — grows as ViDA lands; (3)
  **credential-based portal scraping** (`portal_scraper.py`) for the low-IT supplier long tail (the
  majority); (4) manual upload (existing). All on the Phase-0 worker tier.
- **Credential custody hardening:** envelope encryption (KEK→DEK) + KMS/HSM, **per-tenant/BYOK keys**
  (no bulk-decrypt), **OAuth/scoped tokens over passwords** where supported, least-privilege + full
  audit, rotation.
- **Self-service supplier onboarding:** client stores portal logins / connects APIs; the system
  auto-fetches and analyses.
- **Outcome:** invoices arrive automatically; manual collection disappears; the dataset compounds.
- **Gates:** scraping ToS/consent (scrape a client's OWN account with explicit authorisation);
  security posture toward SOC 2 Type II + ISO 27001/27017/27018.

### Phase 2 — Revenue: direct-to-fleet recovery + expense/export
*Turn the captured data into recovered cash and a finance-department product.*
- **Direct-to-fleet recovery wedge** — automated 2008/9/EC VAT + diesel-excise + toll-VAT recovery
  across ALL the fleet's fuel cards (the multi-card edge captive schemes can't match). Pricing:
  **contingency % of recovered VAT + thin per-vehicle SaaS fee.**
- **Company expense reports** — per-vehicle/driver expense, mileage/per-diem on the existing
  line-item data (no receipt-OCR step).
- **SAF-T / e-invoice / ERP export** (Xero/QuickBooks/DATEV/Sage) — rides the mandatory ViDA +
  national e-invoicing tailwind (2026-2030); widens the buyer to the finance team; future-proofs
  ingestion.
- **Overcharge → recovery workflow** — group flagged overcharges into a "claim-back" packet → supplier credit.
- **Refund forecasting & cash-flow view** (M3 data layer exists).

### Phase 3 — Profit centre: embedded finance + open banking (partner-gated)
*Monetise the cash-timing of the refund — the highest-margin layer; proven by FastVAT/Eurowag.*
- **Embedded finance** — advance/factor the VAT-refund receivable via a **licensed factoring
  partner** (Factris-style: our UI + partner licence; ~90% advance, ~1-3% fee, funds in days). We
  monetise origination/margin with NO licence. A tax-authority receivable is a high-certainty asset,
  and we can underwrite it because we see it line-by-line.
- **Open-banking reconciliation + pay-by-bank** via an aggregator/agent (Tink/TrueLayer/Yapily) —
  reconcile fuel-card statements ↔ bank payments; initiate supplier/refund payouts at <1%. **Agent
  of a licensed provider — do NOT self-licence.**
- **Gates:** factoring/credit licensing is per-Member-State (counsel); CJEU treats factoring fees as
  VAT-taxable; PSD2/PSD3 + partner due-diligence.

### Phase 4 — Intelligence, scale & the SaaS surface
*Compound the data advantage; make it a sellable platform.*
- **Multi-tenant SaaS hardening** — tenant context enforced at every query (RLS / tenant-scoped
  keys), automated cross-tenant access tests, per-tenant encryption. *A cross-tenant leak is a GDPR
  Art. 33/34 breach — isolation must be provable.* + SOC 2 / ISO certification.
- **Validated Postgres cutover** (`MANUAL.md#scaling-the-fleet-fuel-vat-refund-system`) — for multi-client volume.
- **External pooled benchmark (SALE)** — anonymised cross-fleet fuel-price/markup intelligence, via
  an **independent-trustee / aggregation** model (2023 Horizontal Guidelines; hub-and-spoke risk) +
  counsel. Internal benchmark already shipped.
- **SaaS-to-agencies (white-label)** the recovery engine; **API v2** (rate-limits/quotas, key-expiry,
  write/extract scopes).
- **AI copilot** over the audited data (grounded, no hallucinated figures); e-filing integrations
  with tax-authority portals; driver/ops mobile surface (nearest cheap station).

---

### Sequencing principles
1. **Capture before monetise** — Phase 1 (self-feeding data) unlocks every revenue phase and the moat.
2. **Don't run fetching inline** — always the worker tier + per-supplier rate-limiting (don't
   overload our system or the supplier's portal).
3. **Money & risk beat polish** — recover VAT / catch overcharges / prevent a deadline miss first.
4. **Partner, don't self-licence** — embedded finance (factoring partner) and open banking (AISP/PISP
   agent) are partner-gated; build the in-repo seam, let the regulated partner carry the licence.
5. **Keep the guardrails** — every surface honours NET-EUR final prices, `money.py` quantisation,
   escaped output, audited changes, admin-only VAT module, and (new) credential envelope-encryption +
   provable tenant isolation. Evolution must not erode the compliance backbone.
6. **Measure the outcome** — judge each item against the KPIs (€ recovered, days-to-refund,
   % auto-captured, hours/close, ARR, financing €).

### Cash-recovery product pivot (current focus — north star: € recovered · € overcharges · days-to-refund · deadline misses = 0)
The active product direction is to present the platform as a **cash-recovery product, not a general expense tool**. Shipped first:
- **Cash-recovery ROI dashboard** (`vat_refund.recovery_dashboard` → `/recovery-dashboard`): the value-first surface — recoverable VAT (in flight / claimable now), supplier overcharges, € recovered, deadline risk, days-to-refund, over the six claim-readiness states (Ready · Deadline risk · Missing documents · Below threshold · Submitted · Paid). Built on the canonical `claims_overview`+`recovery_report`.
- **Capture reads the legal entity off the invoice** (per-country seller; marker-only matching; detection leads with the entity) + **per-country entity learning** so the right entity lands on the claim.
- **Audit snapshot** (highlighted supplier/client duplicate) · **multi-company sales invoicing** + **company-onboarding gate**.

Next in order (do these; defer AI chat / broad expense / public benchmark / own finance licence):
1. **Overcharge evidence-packet + claim-back workflow** — turn `contract_audit` €-overcharges into a supplier claim-back (reuse `evidence_pack`).
2. **"Upload last quarter → see refund opportunity"** acquisition flow (the best-first-offer landing).
3. **Capture automation inbound** — email inbox / API-EDI / e-invoice (less manual upload = more value), then portal scraping.
4. **ERP exports** — Xero / QuickBooks / DATEV on top of the existing Excel / ledger-CSV / SAF-T / e-invoice hub.
5. **Security before SaaS** — tenant isolation (per-table `scope_clause` phase) · encrypted credentials (done seam) · audit logs · DPA/security docs.

### Near-term concrete steps (from `#backlog`)
- Phase 0: D6 worker tier · per-supplier rate-limiter · test coverage · one-click close.
- Phase 1: API ingestion + portal-scraping (both) on the worker tier · credential-custody hardening.
- Phase 2: SAF-T/e-invoice/ERP export · expense reports · direct-to-fleet recovery packaging.

---

### Costed deep-dive — each idea, build/effort/$/timeline

A concrete, costed, sequenced deep-dive of **each idea** in the phases above. Each item: **what + why it's worth it · how to build it
(the in-repo seam) · effort/cost · dependencies · #1 risk · success metric.** Grounded in the
codebase + three deep-research passes (figures are indicative ranges with confidence; provider
prices are quote-based — model, don't quote). Currency mixes EUR/USD as sourced.

#### The "make-it-worth-it" milestone (north star)
The platform crosses from **cost-centre → sellable product** when it is **self-feeding and
multi-tenant with at least one paying external customer on a repeatable contract.** Concretely:
(1) invoices flow in **automatically** for most suppliers (API/e-invoicing + scraping) with no
manual upload; (2) a **second, unrelated company** is onboarded behind **provable tenant
isolation**; (3) at least one **revenue line is live** (recovery contingency, a per-vehicle
subscription, or financing origination). Everything below ladders to that.

---

#### Phase 0 deep-dive — Foundation (Q1; in-repo, no partner) — *unblocks everything*

**0.1 Dedicated worker tier (D6).** *Why:* fetching/scraping is slow/bursty/external; it must run
off the web path or it overloads the app. *Build:* `FFS_ROLE=web` for web nodes; run scrapers as a
separate `python waiting_room.py --work` process/host — the durable queue (`waiting_room.py`) +
`process_lock` leader election already exist. *Effort:* S–M (days). *Dep:* none. *Risk:* low.
*Metric:* web p95 latency unaffected by a fetch burst.

**0.2 Per-supplier rate-limit / concurrency / backoff / circuit-breaker.** *Why:* protects your
system AND keeps you under each portal's anti-bot/ban threshold. *Build:* a token-bucket + max
in-flight per `(supplier)` on the queue claim path; exponential backoff (Celery-style 1/2/4s); a
circuit-breaker that parks a supplier's jobs on repeated failure. *Effort:* M. *Dep:* 0.1.
*Risk:* med (get the per-supplier keying right). *Metric:* zero portal bans; no thundering herd.

**0.3 One-click monthly close.** *Why:* removes a half-day of CLI babysitting. *Build:* one guarded
button chaining `engine_close.py` with a live progress log (the upload-receipt pattern exists).
*Effort:* S. *Metric:* close time minutes, not a half-day.

**0.4 Test coverage** for `invoice_control`/`ingest`/`build_master`/`history`. *Why:* ship velocity
+ no money drift. *Effort:* M. *Metric:* coverage on the engine path; green before each phase.

---

#### Phase 1 deep-dive — Automated document capture (Q1–Q3; the moat engine)
*The most important build: makes the platform self-feeding and accumulates the proprietary
multi-network line-item dataset. Build BOTH paths; lead with structured.*

**1.1 Supplier API / EDI ingestion.** *Why:* cleanest, most reliable capture where a supplier has
an API. *Build:* extend `ingest.py` / `/api/*` with per-supplier connectors enqueued to the worker
tier. *Effort:* M–L (per connector). *Risk:* few fuel suppliers expose APIs (low coverage).
*Metric:* % of volume via API.

**1.2 E-invoicing inbound (PEPPOL / EN-16931).** *Why:* mandatory e-invoicing (ViDA 2030; national
2026–2028) makes invoices arrive machine-readable — capture for free as it lands. *Build/Buy:*
**BUY a PEPPOL Access-Point-as-a-Service** (Storecove/EDICOM/Pagero/Unifiedpost) — self-certifying
needs OpenPeppol membership + ISO 27001 + BIS impl, not worth it; **parse EN-16931 in-house.** The
**`factur-x` Python lib (akretion, BSD)** is **directly reusable for the existing Factur-X/ZUGFeRD
`parse_einvoice` branch** (`get_xml_from_pdf`, EN-16931 XSD/Schematron validation, profile
detection). Country clearance (Poland **KSeF** — REST API + `ksef2` Python SDK; France
Factur-X/Chorus-Pro/PDP; Italy SdI/FatturaPA; Germany XRechnung/ZUGFeRD) is reached *through* the
AP. *Cost:* AP-as-a-service ~€0.18/invoice floor (mostly quote-based). *Effort:* M (AP integration)
+ S (reuse factur-x). *Risk:* per-country CIUS variance. *Metric:* % via e-invoice, rising over time.

**1.3 Credential-based portal scraping (the low-IT long tail).** *Why:* most fuel/toll suppliers
have no API/e-invoicing — scraping is a **required first-class capability**, not just a fallback.
*Build:* **Playwright (Python)** over Selenium (auto-wait kills selector flakiness) on the worker
tier; per-adapter logic in the existing `portal_scraper.py` seam; **containerised browser workers**
(headless costs 10–50× plain HTTP); handle MFA/CAPTCHA; session/cookie reuse. *Running cost:*
residential proxies ~$3–9/GB, CAPTCHA-solving ~$0.5–3/1000. *Effort:* L + **continuous
maintenance** (portals change markup — the real cost). *Risk:* fragility + ToS/anti-bot — scrape
only a client's OWN account with explicit written authorisation (the PSD2 precedent moved bank data
*off* scraping to APIs; treat scraping as transitional, retire per supplier as APIs/e-invoicing
arrive). *Metric:* fetch success rate per supplier; maintenance hours/portal/month.

**1.4 Credential-custody hardening (the security concern).** *Why:* you're holding clients' portal
logins — custody is a liability (GDPR Art. 33/34, 72-hour breach notice). *Build:* **envelope
encryption** (KEK in KMS wraps a per-secret DEK) backed by **cloud KMS** (AWS/GCP/Azure ~per-key +
per-call) **or HashiCorp Vault**; **per-tenant/BYOK keys** so the platform can't bulk-decrypt one
client's secrets; **prefer OAuth/scoped tokens over stored passwords** where a supplier supports it
(the CyberArk/UiPath "credential vault" pattern is the analogue); least-privilege + full audit +
rotation. *Cost/effort:* cloud KMS **~$1/key/mo + ~$0.03/10k ops** (AWS/GCP/Azure converge) or
**Vault OSS free (Transit engine)** — HCP Vault Dedicated ~$450–1150+/mo (HCP Vault Secrets was
sunsetted mid-2025, don't design around it); implementation **~1–3 eng-weeks** single-KEK, **+1–3
weeks** for per-tenant/BYOK keys (~$1/tenant/mo + data-key caching to stay under the KMS quota).
Targets: the `portal_scraper.py` credentials + IBANs (aligns with the existing "AI never sees
IBAN/secret" rule). **Decision:** KMS vs Vault. *Risk:* a credential-store breach is existential —
this gates self-service onboarding. *Metric:* no plaintext at rest; every access audited; pen-test.

**1.5 Self-service supplier onboarding.** *Build:* client connects an API / stores portal logins;
system auto-fetches + analyses. *Effort:* M. *Dep:* 1.2–1.4. *Metric:* onboarding without engineering.

---

#### Phase 2 deep-dive — Revenue (Q3–Q4; turn data into cash + a finance-dept product)

**2.1 Direct-to-fleet recovery — packaging + pricing.** *Why:* the multi-card wedge captive
schemes can't match. *Pricing (hybrid):* **success fee ~15–30% of recovered VAT** (no-win-no-fee,
the sector norm) **+ a per-vehicle subscription (~$20–60/veh/mo equivalent)** for the analytics/
expense side. Size the contingency against realistic per-claim recoveries (Dir. 2008/9/EC minimums
€400 quarterly / €50 annual cap small claims). *Build:* mostly packaging on the existing recovery
engine + billing. *Metric:* € recovered, days-to-refund, contingency € booked.

**2.2 Company expense reports.** *Why:* the line-item fuel/toll/AdBlue data already exists → expense
+ mileage/per-diem with **no receipt-OCR step** (the edge over Pleo/Spendesk/Concur). *Build:* new
report surfaces + per-vehicle/driver rollups on `transactions`. *Effort:* M. *Metric:* expense
reports generated; attach rate.

**2.3 SAF-T / e-invoice / ERP export.** *Why:* rides the mandatory ViDA tailwind; widens the buyer
to the finance team; future-proofs ingestion. *Build (effort low→high):* **QuickBooks Online**
(free sandbox, clean REST) < **Xero** (8+ certification checkpoints, 3-customer gate, new tiered
API pricing Mar-2026) < **DATEV** (batch/file-job, consultant + validation-heavy — the big German
lift; mitigate via a certified unified-API partner e.g. Maesn/Chift). **SAF-T is a separate
per-country mapping workstream** (OECD schema → Poland JPK_V7M, Romania D406, Portugal, Norway…),
not one file. *Effort:* L (multi-quarter). *Metric:* # accounting integrations live; SAF-T files
accepted.

**2.4 GTM / first paying fleets.** *Motion:* narrow **EU-transport vertical wedge**; lean on
**fuel-card/telematics channel partnerships + referrals** over pure outbound (cold-email booking
rates ~halved 2024→2026). *Targets:* SMB CAC ~$200–500, **<12-mo payback, ≥2.5:1 LTV:CAC.** *First
beachhead:* fleets using **2+ card networks** (the fragmentation you uniquely solve).

---

#### Phase 3 deep-dive — Profit centre (Q4–Q6; embedded finance + open banking — partner-gated)

**3.1 Embedded finance — advance/factor the VAT receivable.** *Why:* highest margin; monetises the
4–8-month refund wait; proven (FastVAT/Eurowag). *Build/Buy:* **BUY — originate-and-refer to a
licensed factoring partner**; do NOT take balance-sheet risk or a licence. **Factris** (NL/Lithuania
— Baltic fit; "up to 3%" *time-based* fee; €100M facility; an explicit partner program) is the lead
candidate. *In-repo seam:* M3 already built the receivable data layer + the route-aware settlement;
add the partner-API hand-off + status sync. *Unit economics:* a VAT refund is owed by a **tax
authority → near-zero default**, timing 4–8 months with **statutory late interest (Art. 26–27)**
capping downside; expected loss = **partial-approval haircut, not insolvency** → a **recourse
structure keyed to the approval haircut**, advance **80–95%** below the *expected approved* amount,
time-based discount. *Platform take:* origination/rev-share **1–5%/deal** (negotiated). *Vertical
SaaS embedding finance reportedly lifts revenue/customer 3–4×.* *Effort:* M (integration) + L
(partner contracting/legal). **Risk/gate:** factoring is regulated lending (CRD Annex I), per-Member
-State; CJEU treats factoring fees as VAT-taxable; **partner does diligence — confirm a live VAT-
receivable product with Factris** (no clean public precedent found). *Metric:* financing origination
€; advance turnaround (target ~24h).

**3.2 Open banking — reconciliation + pay-by-bank.** *Why:* close the statement↔payment↔refund↔
financing loop; pay at **<1% vs 1.5–3.5% cards.** *Build/Buy:* **BUY — operate as an AISP/PISP
AGENT under a provider's licence** (Tink/TrueLayer/Yapily all hold both; agent route compresses the
regulatory timeline to weeks). Usage-based, quote-only pricing. *In-repo seam:* the reconciliation
engine already exists; add the aggregator API + the open-banking ingest as another `waiting_room`
job kind. *Effort:* M. *Risk:* PSD2/PSD3 + partner diligence. *Metric:* statements auto-reconciled
%; pay-by-bank volume.

---

#### Phase 4 deep-dive — SaaS & scale (Q5–Q8; make it a sellable platform)

**4.1 Multi-tenant isolation.** *Why:* one client must NEVER see another's data — a cross-tenant
leak is a GDPR Art. 33/34 breach (fines to €20m/4%) and a trust/contract failure. *Build (recommend
**PostgreSQL Row-Level Security**):* `tenant_id` on every table; an RLS policy per table keyed on a
session `app.tenant_id` SET per connection → the DB enforces isolation so a forgotten `WHERE
tenant_id` (the OWASP-API-#1 IDOR/BOLA class) can't leak; tenant context in every request; automated
cross-tenant access tests; per-tenant encryption keys (ties to 1.4). **TWO critical footguns that,
if missed, silently re-open the leak:** (1) RLS is bypassed by the table OWNER, superusers, and
`BYPASSRLS` roles — so run app traffic as a **non-owner, non-superuser role** AND set `ALTER TABLE …
FORCE ROW LEVEL SECURITY`; (2) under a transaction-pooling pooler (PgBouncer), a session `SET` leaks
into the next request — use **`set_config('app.tenant_id', …, true)` (transaction-local)**, not a
session `SET`. RLS is defense-in-depth UNDER app-layer scoping (still need per-object authZ within a
tenant). DB-per-tenant is the max-isolation alternative for large/regulated tenants (hybrid common:
RLS for the long tail). *Migration path* from today's per-deployment model: add `tenant_id` to every
table, thread a tenant context through `db.connect()`, enable RLS+FORCE, load each per-customer
SQLite DB into shared Postgres under its `tenant_id`, move sessions/queue to the shared DB. *Effort:*
L. **Decision:** RLS vs DB-per-tenant + go-multi-client-now-or-stay-per-deploy.
*Risk:* the highest-stakes correctness work — isolation must be *provable*, not assumed. *Metric:*
automated cross-tenant tests pass; a pen-test finds no leak.

**4.2 Security certification (SOC 2 Type II + ISO 27001).** *Why:* the diligence gate to sell to
larger/EU-regulated buyers, mandatory once you hold credentials + financial data. *Cost/timeline:*
SOC 2 Type II ~**$20–40k** first year (~6–15 mo, gated by the 3–12-mo observation window); ISO 27001
~**$15–30k** (~3–8 mo); **both together ~$40–80k all-in, ~6–12 mo, ~$25–60k/yr ongoing, ~0.25–0.5
FTE** during the push (65–75% control overlap → do them together). Use an automation platform
(Vanta/Drata/Secureframe/Sprinto ~$7.5–25k/yr). *Trigger:* start ~3–6 mo before the first
enterprise deal, not after losing one. *Metric:* SOC 2 Type II report + ISO cert issued.

**4.3 Validated Postgres cutover** (`MANUAL.md#scaling-the-fleet-fuel-vat-refund-system`) — exercise `_PgShim` on real psycopg, port
dialect-isms (`datetime('now')`→`now()`, `INSERT OR IGNORE`→`ON CONFLICT`, audit triggers →
PG trigger fn), move queue + leases to the shared DB, `pg_dump` backups. *Why:* the substrate for
multi-tenant volume. *Effort:* L (needs a live Postgres). *Metric:* full suite green on PG.

**4.4 External pooled benchmark (SALE).** *Why:* a premium data product on the accumulated dataset.
*Build/Gate:* anonymised cross-fleet fuel-price/markup intelligence via an **independent-trustee /
aggregation** model (2023 Horizontal Guidelines; **hub-and-spoke** risk; no firm-level/forward data)
+ counsel + the Data-Act unfair-terms regime. Internal benchmark already shipped. *Effort:* M + legal.
*Metric:* benchmark subscribers; counsel sign-off.

---

#### Cross-cutting

**Build vs buy.**
| Capability | Decision | Why |
|---|---|---|
| Portal scraping (Playwright + queue) | **BUILD** | core data acquisition + the moat; extends existing seam |
| EN-16931 / Factur-X parsing | **BUILD** (`factur-x` lib) | reuses the existing branch |
| PEPPOL Access Point | **BUY** | certifying needs ISO 27001 + membership; not the product |
| KMS / secrets vault | **BUY** (cloud KMS or Vault) | don't roll your own crypto |
| Anti-bot infra (proxies/CAPTCHA) | **BUY** (if needed) | commodity; keep orchestration + data in-house |
| Factoring of the receivable | **BUY/partner** (Factris-style) | needs a lending licence + balance sheet |
| Open banking (AIS/PIS) | **BUY/partner** (agent of Tink/TrueLayer/Yapily) | months of authorisation for no differentiation |
| Accounting connectors | **BUILD** (QB/Xero) / **partner** (DATEV unified-API) | DATEV is consultant-heavy |
| SOC 2 / ISO | **BUY tooling** (Vanta/Drata) + auditor | standard path for small teams |

**Team/skills.** Beyond current eng: a **compliance/tax-domain owner** (gating for fintech credibility
and the cert push), **partnerships** (factoring/open-banking/channel), and **sales** for the
direct-to-fleet motion. The cert push needs ~0.25–0.5 FTE of a security-minded engineer for a few
months.

**Capital/runway.** This is fundable as an EU vertical fintech — **seed median ~€1.5–2M (2025)** —
but investors want demonstrated compliance/tax-domain credibility before writing. The plan is
sequenced so revenue (Phase 2) can start before the heavy cost (Phase 4 certs, Postgres, partner
legal), reducing the capital needed to reach the milestone.

---

#### The recommended 18-month sequence (quarter by quarter)
- **Q1** — Phase 0 (worker tier, per-supplier rate-limiter, one-click close, test coverage) + start
  Phase 1.4 (KMS/credential custody design). *Foundation; unblocks capture.*
- **Q2** — Phase 1.2 (PEPPOL AP + reuse `factur-x`) + 1.3 (Playwright scraping for the top low-IT
  suppliers) + 1.4 (credential custody live). *Self-feeding begins.*
- **Q3** — Finish Phase 1 (1.1 API connectors, 1.5 self-service onboarding) + start Phase 2.1
  (recovery packaging + pricing) and 2.2 (expense reports). *First revenue motion.*
- **Q4** — Phase 2.3 (QuickBooks/Xero + first SAF-T country) + 2.4 (land first paying fleets via
  channel) + scope Phase 3.1 (Factris partner contracting). *Revenue + finance partner signed.*
- **Q5** — Phase 3.1 (embedded-finance origination live) + start Phase 4.1 (multi-tenant RLS) and
  4.2 (begin SOC 2 observation window + ISO ISMS). *Profit centre + isolation.*
- **Q6** — Phase 3.2 (open-banking agent) + finish 4.1 (provable tenant isolation) + 4.3 (Postgres
  cutover). *Second external tenant onboarded = the milestone.*
- **Q7–Q8** — SOC 2 Type II + ISO certs issued; Phase 4.4 (benchmark sale, counsel-gated); API v2;
  scale the direct-to-fleet + financing engine. *Enterprise-sellable.*

#### The 3 decisions the owner must make FIRST
1. **Go multi-client SaaS now, or stay per-deployment for the Baltic entities first?** — gates the
   multi-tenant RLS work (4.1) and how early Phase 4 starts. (Recommend: build capture + revenue
   single-tenant first; add multi-tenancy when the first external customer is real.)
2. **KMS backend: cloud KMS (AWS/GCP/Azure) vs HashiCorp Vault?** — gates credential custody (1.4),
   the prerequisite for self-service onboarding and the whole scraping programme.
3. **Embedded-finance partner: pursue Factris (or another licensed factor) for a VAT-receivable
   advance product?** — the highest-margin lever; needs early outreach (no public precedent — confirm
   a live product), and it shapes whether Phase 3 is real.

#### Make-it-worth-it milestone (restated, measurable)
Reached at **~Q6** when: (a) the **majority of invoices arrive automatically** (API/e-invoice +
scraping, no manual upload); (b) a **second, unrelated company runs on the platform behind
provably-isolated multi-tenancy**; (c) **≥1 revenue line is live** (recovery contingency, per-vehicle
subscription, and/or financing origination). At that point it has crossed from internal cost-centre
to a **sellable, revenue-generating product** — and the proprietary multi-network dataset (the moat)
is compounding. Certifications (Q7–Q8) then unlock the larger/enterprise + EU-regulated buyers.

*Sources: three deep-research passes (EU VAT-refund market; expense/e-invoicing; open banking +
embedded finance; fleet/mobility landscape; data/competition-law gates; transport-SME pain;
automated-fetching patterns; credential-custody security; multi-tenant isolation; scraping legality;
+ this pass — scraping/e-invoicing build, KMS/multi-tenant, SOC2/ISO cost, factoring/open-banking
integration, GTM/pricing/accounting). Inline figures are indicative ranges with the confidence flags
recorded in those passes; provider prices are quote-based — model, don't quote.*

---

## Backlog

Consolidated outstanding work, synthesized from this project's audits/research
(`#deep-research-findings-logic-flaws-monetization`, `MANUAL.md#process-reliability-stuckstall-risks-hardening`, `../README.md#data-architecture-duplication-under-used-data`, `../README.md#the-platform-seven-delegated-works`, `MANUAL.md#ai-review-assistant-advisory-validation-analytics`,
`#product-roadmap`, `MANUAL.md#scaling-the-fleet-fuel-vat-refund-system`) and the in-flight programs. Grouped by **readiness**:
ready-now (no decision needed) → decision-gated → strategic. Each item notes its source,
rough effort (S/M/L), risk, and any product decision that gates it.

Already SHIPPED (not repeated below): decoupling D1–D5; the reliability sprint (orphan-watcher,
stuck-job surfacing, notify digest + scheduler, monitoring panel, upload-gate, startup orphan-sweep,
notify-on-success, `rejected` keeps locks, DLQ alerting + oldest-pending-job age SLO, register-failure
reconcile, backup-close guard, atomic `record_payment`); the §B VAT-correctness suite (goods-code,
falsy-zero, quarterly fee-base, national-currency threshold hard-gate + override, receipt-control
gate + waive, FX provenance + per-invoice ECB verification) + the frozen-VAT clobber fix; the
pricing-correctness fixes (period bucketing, volume-weighted pack mean); the under-used-data analytics
runway (time-of-day, per-vehicle €/L, parser-priority, import/audit trends, reliability scorecard); the
whole `except: pass`→`applog` migration; the money-precision sweep; the CRM integration API; the full
document-management module; and `.docx`→PDF generation. The items below are what remains **open**.

**Strategic direction:** see the strategy above (monetisation models + data-acquisition
architecture) and `#product-roadmap` (phased plan). The flagship near-term programme is **automated
document capture** (below) — it makes the platform self-feeding and builds the dataset moat.

---

### Flagship — Automated document capture (Roadmap Phase 1; the moat engine)
Build BOTH capture paths and run them OUT-OF-BAND on a dedicated worker tier (never inline). Lead
with structured (API/e-invoicing); credential-scraping is first-class for the low-IT supplier tail.
- **Dedicated worker tier (D6)** — `FFS_ROLE=web` + `python waiting_room.py --work`; the execution
  substrate for fetching/scraping. *(M, low — prereq for the rest.)*
- **Per-supplier rate-limit / concurrency cap / backoff / circuit-breaker** on the intake queue —
  so fetching can't overload our system or trip a supplier's anti-bot/ban. *(M)*
- **Supplier API / EDI ingestion** — advance `ingest.py` / `/api/*` to pull where a supplier offers
  an API. *(M–L)*
- **E-invoicing inbound** — PEPPOL/EN-16931 + email/invoice-inbox capture (grows as ViDA lands). *(L)*
- **Credential-based portal scraping** — advance `portal_scraper.py` adapters for low-IT suppliers
  (login → fetch invoices/statements → enqueue). Handle MFA/CAPTCHA fragility; fall back to manual. *(L)*
- **Credential-custody hardening** — envelope encryption (KEK→DEK) + KMS/HSM, **per-tenant/BYOK keys**
  (no bulk-decrypt), OAuth/scoped tokens over passwords where supported, least-privilege + audit,
  rotation. *(M, security-critical.)* **Decision:** KMS/secrets backend (cloud KMS vs Vault).
- **Self-service supplier onboarding** — client connects API / stores portal logins; system
  auto-fetches + analyses. *(M)*
- **Multi-tenant isolation (if multi-CLIENT SaaS)** — tenant context enforced at every query (RLS /
  tenant-scoped keys), automated cross-tenant access tests, per-tenant encryption; a cross-tenant
  leak is a GDPR Art. 33/34 breach. *(L)* **Decision:** tenancy model (RLS vs schema vs DB-per-tenant)
  + whether to go multi-client SaaS now or stay per-deployment.

---

### A. Ready now — no decision needed (ordered by value)

#### Reliability (MANUAL.md#process-reliability-stuckstall-risks-hardening)
- **`process_lock` fencing token + monotonic-clock deadline.** *(M, med)*
  — ⏸️ SCALE-GATED (assessed): fencing tokens prevent a stale lease holder's writes under
  MULTI-process contention; the lease-expiry/double-write window only bites with several
  worker processes. For the single-box default (one intake worker thread per process) it's
  low-urgency. Do before/with the validated Postgres/horizontal-scale cutover (§D).
- **Per-job extract deadline / lease sizing.** *(M, med)*
  — ⏸️ SCALE-GATED (assessed): a lease expiring mid-extract only causes double-processing
  with MULTIPLE worker processes (one worker is single-threaded, so it can't reclaim its own
  in-flight job). Single-box risk is low. Lighter wins if pursued: cap ZIP members / bound the
  pypdf probe / confirm the AI backend has a request timeout — but no fragile thread-kill timeout.

#### Data de-duplication (../README.md#data-architecture-duplication-under-used-data Part 1)
- **Derive `gross`/`gross_local`** instead of storing net+vat (or add a CHECK). *(S, low)*
  — ⏸️ DEFERRED (assessed): the values don't drift (statement_invoices REPLACE-syncs;
  transactions gross derived in views), and drop/CHECK both need a full SQLite table
  REBUILD of engine-owned tables — net-negative ROI for harmless redundant storage.
- **Persist only receipt-control overrides** (`waived`/`note`); derive `status`/`expected`. *(S, low)*
  — ⏸️ DEFERRED (assessed): the stored `expected`/`status` double as a point-in-time
  snapshot at `checked_at` (audit value); reworking the engine writer + read path is low ROI.
- **Document path-migration completeness** — confirm a SharePoint/FTPS backend migration
  re-points `stored_path` in `intake_jobs`/`data_lake_files`, not just `invoice_documents`. *(S, med)*
  — → folded into the reliability batch (storage-backend split-brain).
  (The Part-2 under-used-data analytics — reliability scorecard, time-of-day/off-hours, per-vehicle
  €/L, data-lake confidence→parser-priority, import/audit trends — all SHIPPED.)

#### Decoupling completion
- **D6 — intake worker as a dedicated worker-process by default** (web nodes set
  `FFS_ROLE=web`, a separate `python waiting_room.py --work`); docs + sample unit. *(M, low)*

#### Code-quality
- **Test coverage** for `invoice_control`/`ingest`/`build_master`/`history` (some added). *(M)*
  (The `except: pass`→`applog` migration and the money-precision sweep are SHIPPED.)

---

### B. Decision-gated — needs a product call first

#### Logic correctness (#deep-research-findings-logic-flaws-monetization Part 1)
- **VAT thresholds in national currency** for non-EUR refund countries (SE/DK/PL) — €400/€50
  is currently compared in EUR. **Decision:** confirm the per-currency minimums (SEK 4 000/500,
  PLN/DKK equivalents). *(M, high value)*
- **Threshold actually gates submission** (below-min blocks or requires override) — today
  it's verdict-text only, so a sub-threshold quarter can be filed and rejected, locking
  invoices out of the annual mop-up. **Decision:** hard gate vs warn. *(M, high)*
- **Deferral coverage** — assert every VAT-bearing quarter is filed-or-mopped before Sept-30.
  **Decision:** force-pull deferred quarters into the annual claim, or allow intentional gaps. *(M)*
- **Country diesel recoverability / pro-rata** — no logic exists. **Decision:** in scope, or
  handled upstream? *(? )*

#### Data architecture (../README.md#data-architecture-duplication-under-used-data)
- **FX provenance + single source of truth** — store the applied rate (or its `ecb_fx` key)
  per line/period so a claim's EUR is traceable. **Decision:** is `month_config.FX`
  (hard-coded `1/4.27`) authoritative, or should it derive from `ecb_fx`? *(M, med)*
- **Promote `card` to the transaction grain?** (enables per-card analytics; needs a schema
  change). **Decision:** card vs vehicle as the unit.
- **`wholesale_prices` default source** so `margin_vs_wholesale` isn't silently inert.
  **Decision:** ship a default market source (EU Oil Bulletin) or keep opt-in.

#### Automation program — Phase 2 (../README.md#the-platform-seven-delegated-works / the automation plan)
- **Scheduled portal scrape** — **Decision:** which portals are authorized for unattended
  scheduled pulls. *(M, med)*
- **Scheduled API ingest** (DKV/E100) — **Decision:** which APIs authorized + tokens exist. *(M, med)*
- **Receipt-control required-set into the submission gate** — **Decision:** does a MISSING
  required invoice **block** submission or **warn**? *(M, high)*

#### Automation program — Phase 3
- **`parse_dkv()` / `parse_e100()` deterministic parsers** — **Prereq:** 2–3 redacted DKV
  and E100 sample invoices. *(M each)*
- **Auto-attach a document to its invoice by parsed invoice_no.** *(S–M)*

#### Automation program — Phase 4
- **Structured invoice↔transaction matching** (invoice_no/supplier/amount/period) replacing
  the fragile `note`-substring match, + an **UNMATCHED resolution UI**. Touches legal figures
  — audited, admin-gated. **Decision:** UNMATCHED-resolution authority (processor vs admin). *(L, high)*

#### Automation program — Phase 5 (the confidence-learning model)
- **Per-invoice confidence + validation-event ledger; AI validation returns a score;
  settled→skip-AI; per-supplier-×-country trust that grows with each clean validation.**
  Confidence reduces redundant WORK, never bypasses the legal gates. **Decision:** the
  growth/decay constants (proposed init 0.50, growth `+0.25·(0.95−trust)`, decay −0.30,
  floor 0.10) and the skip-AI / human-review thresholds. *(L, the centerpiece)*

---

### C. Strategic / larger bets

#### Strategy-derived (see `docs/STRATEGY.md` — the prioritised monetisation roadmap)
- **SAF-T / e-invoice / ERP export** — the highest-value capability bet; rides the ViDA +
  national e-invoicing mandates (2026–2030). Widens the buyer to the finance dept; future-proofs
  ingestion. *(M–L)*
- **Embedded-finance partner integration** — factor/advance the VAT-refund receivable via a
  licensed factoring partner (Factris-style: platform UI + partner licence). The profit centre;
  proven by FastVAT/Negométal/Vatecure/Eurowag. M3 built the data layer. *(L, partner-gated)*
- **Open-banking reconciliation + pay-by-bank** — aggregator/agent partner (Tink/TrueLayer/
  Yapily); reconcile fuel-card statements ↔ bank payments, initiate supplier/refund payouts. Do
  NOT self-licence (agent of a regulated provider). *(M, partner-gated)*
- **Company expense reports** — per-vehicle/driver expense + mileage/per-diem on the existing
  line-item transaction data (no receipt-OCR step). *(M)*
- **External pooled benchmark (SALE).** Only if/when selling externally; gated by the full legal
  stack in `#deep-research-findings-logic-flaws-monetization` (EU competition-law hub-and-spoke,
  GDPR anonymization, min-cohort ≥5/no-single->25%, independent trustee, Data Act unfair-terms,
  data-use license) + counsel. **Internal peer benchmark is already shipped.**
- **API follow-ups** — per-key rate-limiting/quotas, key-expiry policy, and a scoped v2
  for any write/extract surface. *(M)*
- **AI review assistant v2** — extend the advisory panel to invoice/claim review surfaces (A6). *(L)*
- **Overcharge → recovery workflow** — turn contract-audit/overpay detection into an
  actionable recovery packet → supplier credit (the analytics "act on it" gap). *(L)*
- **Factur-X follow-ups** — per-file handling of mixed hybrid/plain batches; catalog
  name-tree fallback test. *(S)*
- **Materialize expensive aggregates** (overpay/benchmark/fleet/cycle-time totals) into a
  settled metrics table rebuilt at the close, with a recompute-and-compare drift check
  (../README.md#data-architecture-duplication-under-used-data "in increments"). *(M, med)*

### D. Platform / scaling (CLAUDE.md / MANUAL.md#scaling-the-fleet-fuel-vat-refund-system)
- **Validated Postgres cutover** — exercise `_PgShim` on real psycopg; port dialect-isms
  (`datetime('now')`→`now()`, `INSERT OR IGNORE`→`ON CONFLICT`, audit triggers → a PG
  trigger fn); migrate the intake queue + `process_lock` leases to the shared DB;
  Postgres-native backups (`pg_dump`). *(L, needs a live Postgres)*
  (Off-machine backup sync, per-event alerts + SMTP relay UI, and `.docx`→PDF generation are SHIPPED.)

---

### Consolidated open product decisions (gate the B items)
1. Per-country VAT minimums (SEK/DKK/PLN values) + does the threshold **hard-gate** filing?
2. FX authority: `month_config.FX` vs `ecb_fx`?
3. Receipt-control gate: **block** vs **warn** on a missing required invoice?
4. Which portals/APIs are authorized for unattended scheduled pulls?
5. Redacted DKV/E100 sample invoices (for Phase-3 parsers)?
6. Confidence-learning α/β/thresholds (defaults proposed)?
7. UNMATCHED-resolution authority: processor or admin?
8. Promote `card` to the transaction grain?
9. Diesel recoverability/pro-rata in scope?
10. External SALE of the pooled benchmark — pursue (counsel-gated) or keep internal-only?

---

## Deep-research findings — logic flaws + monetization

Two-part analysis: (1) **software-logic correctness flaws** with fixes (cited to code), and
(2) a **monetization analysis** of the data-processing assets, grounded in business-model and
EU-legal research. Reliability/stall issues are covered separately in `MANUAL.md#process-reliability-stuckstall-risks-hardening`.

> Headline link between the two parts: the **analytics correctness bugs degrade the very data
> product you'd monetize** — fix the math (Part 1) before selling any benchmark (Part 2).

---

### PART 1 — Software logic flaws (prioritized)

> **Status:** the Part-1 correctness flaws are **all SHIPPED** — the goods-code default (`9`→`10`)
> and the falsy-zero guard; the High VAT/fee fixes (national-currency thresholds + submission
> hard-gate, `record_payment`/`paid_amount`, quarterly fee-freeze base); and the Medium analytics
> fixes (period bucketing, volume-weighted pack mean, MoM calendar-month, the `money.f2` sweep, the
> coversheet line-count tie). Retained below for provenance: what was verified CORRECT, and the
> product questions that gated the High fixes (now resolved by the shipped gate/override).

#### Verified CORRECT (don't chase)
Lock lifecycle (3B/3C/3D keep; only `withdraw_claim` releases), `_synthetic` refusal, fee-freeze
is actually frozen, `internal_benchmark` overpay math, div-by-zero guards, rebate handling,
`money.py`, `contract_audit`, anomaly modified-z, ECB nearest-prior FX, and the duplicated overpay
loops (they **agree** — pure tech-debt, safe to consolidate). **Test gaps:** no coverage for
`margin_report`/`pack_avg`/`history` MoM — add tests *before* fixing the period-bucketing/pack-mean.

#### Open product questions (gate the High fixes)
- Is the €400/€50 minimum a hard **gate** or only advisory? (Today: advisory.)
- Is the service fee charged on **claimed** VAT or **actually-paid** refund? (Code intends paid; never captures it.)
- Is country diesel **recoverability/pro-rata** in scope? (No logic exists anywhere.)

---

### PART 2 — Monetization analysis

**Data assets:** validated fuel transactions (5 entities × suppliers × countries), the self-sourced
NET-EUR/L price benchmark + wholesale index, VAT-recovery throughput + the service-fee engine,
contract-compliance/overpay detection, the SHA-256 document vault, the light CRM, the API-plugin seam.
These score well on the monetizability tests (uniqueness, granularity, provenance/trust) — the data is
self-sourced, NET-basis, validated, hashed, audit-logged.

#### The decisive strategy: indirect first
MIT CISR's I-W-S framework + its finding that **~82% of data-monetization returns come from *improving*
operations vs ~18% from *selling* information** → **lead with indirect monetization** (use the pooled
intelligence to lift your own recovery rates and win better fuel deals, priced as savings-share/
contingency). It captures value **without selling data**, avoiding the competition-law/GDPR minefield.
The external benchmark product is a higher-risk Phase 2.

#### Opportunities (prioritized)
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

#### Legal guardrails — what's actually sellable (Opportunity 3 is the gated one)
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

#### Bottom line
1. **Fix Part 1 first** — the fee bugs leak money *today*; the benchmark bugs make Opportunity 3 unsellable.
2. **Opportunity 2** (premium analytics to existing clients) — lowest risk, fastest.
3. **Indirect benchmark → savings-share** (highest expected yield, avoids the legal minefield).
4. **Opportunity 3 external benchmark** only as Phase 2, behind the full guardrail stack **and EU
   competition + data-protection counsel.** Embedded finance (Opp. 5) is the highest-LTV layer.

*This is research synthesis, not legal advice. The price-pooling design in particular requires EU
competition-law and Baltic-jurisdiction counsel before launch.*

---

## Multi-tenancy program plan

This is the program plan for turning the Fleet Fuel & VAT Refund System from a
per-deployment, single-tenant product into a multi-tenant SaaS where several
unrelated client companies share one installation behind **provable tenant
isolation**.

A cross-tenant data leak is a GDPR Art. 33/34 reportable breach. This program is
therefore engineered to **de-risk before it retrofits**: P0 (this slice) ships
the foundation mechanism with **zero behavior change**, and every later phase
moves one table at a time, each gated on an automated cross-tenant test. Nothing
filters or alters an existing query until its table has been migrated AND tested.

> Read this together with `#product-roadmap` (the costed deep-dive, Phase 4.1 — the RLS
> decision + the two footguns) and the strategy above (when multi-client is worth doing). This
> doc does not contradict that analysis; it operationalizes it.

---

### CARDINAL INVARIANT — OFF by default = byte-identical existing behavior

The whole program is armed by a single app setting, `multitenant`, stored in
`app_settings` (security.db) like every other module switch. It defaults to
`"0"` (OFF). While OFF:

- the request hook never binds a tenant context,
- `tenancy.scope_clause()` returns `("", [])` — a literal no-op spliced into no
  SQL, so existing queries are unchanged,
- `tenancy.require_tenant()` does not raise,
- the EnvKEK/key paths and connection roles are untouched.

So a default install behaves EXACTLY as a single-tenant deployment does today.
The full existing test suite must pass unchanged; the OFF-by-default inertness
tests in `tests/test_tenancy.py` are the standing proof of that property.

**Turning the switch ON is gated** on P1+P2 being complete for every
tenant-scoped table (see Activation/rollback below). Flipping it on before then
is a configuration error, not a supported state.

---

### Recommended isolation model

**Application-level `tenant_id` scoping is the foundation**, with PostgreSQL
Row-Level Security (RLS) layered on top as defense-in-depth once we are on
Postgres.

Why this model:

- **Model-agnostic + works now.** SQLite has no RLS. App-level `tenant_id`
  scoping is the only isolation layer available on the default SQLite engine, and
  it is exactly the same mechanism we want on Postgres. Building it first means we
  are not blocked on the Postgres cutover (`MANUAL.md#scaling-the-fleet-fuel-vat-refund-system`), and the same code
  path composes cleanly with RLS later.
- **Defense in depth.** On Postgres we keep the app-level scoping AND add RLS, so
  a single forgotten `WHERE tenant_id = ?` (the OWASP API-#1 IDOR/BOLA class)
  cannot leak — the database refuses the row even if the application query is
  wrong. App scoping is still required (RLS does not give you per-object authZ
  *within* a tenant).

Why NOT the alternatives as the primary:

- **Schema-per-tenant / DB-per-tenant.** Maximum isolation, but operationally
  expensive at scale: every migration must fan out across N schemas/DBs, backups
  and connection pools multiply, and cross-tenant platform queries (billing,
  ops dashboards) get awkward. We do NOT use it as the default.
- **When DB-per-tenant IS warranted:** a single high-value or regulated tenant
  that contractually demands hard physical isolation. The architecture supports
  this as a per-tenant exception (route that tenant's `db.connect()` to its own
  database) while the long tail stays on shared-DB + RLS. This hybrid (RLS for the
  many, dedicated DB for the few) is the common end state.

---

### Owner/operator scope (the audited cross-tenant exception)

Client tenants are isolated from each OTHER — that is the whole point of the
program. But the platform OPERATOR (the business owner) has a deliberate,
single, audited exception: a **READ-ONLY cross-tenant analytics scope**, encoding
the operator's "I as owner must have all analytics data" requirement.

How it is encoded in the foundation (`tenancy.py`):

- The thread-local context holds EITHER a bound client tenant (`set_tenant(tid)`)
  OR the owner marker (`set_owner_scope()`) — **mutually exclusive** (setting one
  clears the other; `reset_tenant()` clears both; `is_owner_scope()` reports it).
- `scope_clause()` returns **no filter** (`("", [])`) under owner scope while the
  switch is ON. This is the **one and only** place `scope_clause` deliberately
  returns no filter with `multitenant` ON — so the operator's analytics span every
  tenant. (When the switch is OFF, `scope_clause` is the no-op for a different
  reason: a single-tenant install correctly sees "everything" because there is only
  one tenant.)
- The request hook in `app.py` resolves the principal **only when the switch is
  ON**: `_is_owner_principal()` (the `owner_users` app setting seam; the real role
  model is P4) → `set_owner_scope()`, otherwise `set_tenant(_resolve_tenant())`.
  On the OFF path the hook does nothing, so this is fully inert by default.

The two guardrails (non-negotiable; see `#security-compliance-evolution-plan-operating-as-a-multi-client-saas` §7):

1. **PII excluded from owner views.** Owner cross-tenant analytics MUST run on
   **de-identified / aggregated** data. Per §7's controller-vs-anonymise decision,
   the lowest-risk path is to keep personal/identifying data (IBANs, driver/vehicle
   identifiers, contacts) OUT of any owner-scope view; the alternative is to be the
   *controller* for that data with its own lawful basis + dual Art. 30 records. P2
   must build owner-scope surfaces on aggregated/anonymised projections, never on
   the raw tenant rows.
2. **Never relay one client's identifiable pricing to another (antitrust).** The
   owner scope is for the OPERATOR's own analytics; it must not become a channel
   that surfaces one client's identifiable current pricing to another client (see
   §3, the benchmark trap). Pooled/benchmark externalisation stays counsel-gated.

Two hard limits keep the exception safe:

- **Writes are ALWAYS tenant-scoped.** Owner scope is READ-only. `require_tenant()`
  STILL raises when the switch is ON and no concrete tenant is bound — even under
  owner scope — so the operator cannot write/mutate tenant data without naming
  whose data it is.
- **Accountability.** `owner_access_audit(resource)` logs (actor + resource) that
  an owner cross-tenant access happened; P2 calls it wherever owner scope widens a
  query beyond a single tenant. It is best-effort and never raises.

Fail-CLOSED corollary: when the switch is ON but the request resolved NEITHER an
owner scope NOR a tenant (a hook that forgot to resolve its principal),
`scope_clause()` returns a matches-NOTHING clause (`" AND 1=0"`) rather than the
no-op — a missing context can never accidentally read across tenants. The owner
scope is the ONLY fail-OPEN path, and only when it is explicitly set.

---

### The RLS footguns (Postgres phase — reused from the roadmap deep-dive, Phase 4.1)

When we enable RLS on Postgres, two footguns silently re-open the leak if missed:

1. **RLS is bypassed by the table OWNER, superusers, and `BYPASSRLS` roles.**
   The app must connect as a **non-owner, non-superuser** role, AND every
   tenant-scoped table must be set `ALTER TABLE … FORCE ROW LEVEL SECURITY` (so
   the policy applies even to the owner). Owning-the-table is the default for the
   role that ran the migrations; the app role must be distinct.

2. **Session `SET` leaks across requests under a transaction-pooling pooler**
   (PgBouncer transaction mode). Set the tenant **per transaction** with
   `set_config('app.tenant_id', <id>, true)` (the `true` = transaction-local),
   never a session `SET`. The RLS policy reads `current_setting('app.tenant_id')`.

SQLite has no RLS, so on SQLite the **app-level `scope_clause`/`require_tenant`
scoping is the only isolation layer** — which is precisely why P2 makes the app
scoping mandatory and test-gated on every table, independent of the engine.

---

### Phased rollout

Each phase is independently shippable. The `multitenant` switch stays OFF until
the gate in Activation/rollback is met.

- **P0 — Foundation (THIS SLICE).** `tenancy.py`: tenant registry (in security.db,
  app-owned platform metadata), request-scoped thread-local tenant context
  (mirrors `audit.py`'s actor), the `multitenant` master switch (OFF by default),
  and the inert enforcement primitives `require_tenant()` / `scope_clause()`. The
  request hook in `app.py` binds/resets the context ONLY when the switch is ON.
  A read-only `/admin/tenants` surface. Includes the **owner/operator scope** (the
  audited cross-tenant read exception — see above). **No existing product query is
  touched.**

- **P1 — Add `tenant_id` + backfill.** Add a `tenant_id` column to each
  tenant-scoped product table via `db_migrate.apply(...)` (append at the END of
  each module's list). Backfill the existing single tenant's rows to a bootstrap
  `tenant_id` (e.g. `"default"`) so the current installation becomes "tenant
  default" with no data movement. Index `(tenant_id, …)` on hot paths. Still no
  query scoping — the column exists but is not yet read.

- **P2 — Wire scoping into every tenant-scoped query, table-by-table.** For each
  table, splice `scope_clause()` into its SELECT/UPDATE/DELETE (and stamp
  `current_tenant()` / `require_tenant()` on INSERT), and add a **cross-tenant
  access test** that seeds two tenants and asserts no bleed (see strategy below).
  On Postgres, also create the RLS policy + `FORCE ROW LEVEL SECURITY` for that
  table. One table = one reviewable change = one test. A table is "done" only when
  its cross-tenant test is green.

- **P3 — Connection + key enforcement.** Run app traffic as the non-owner DB role
  and enforce `set_config('app.tenant_id', …, true)` per transaction in
  `db.connect()`. Wire per-tenant EnvKEK/BYOK keys (`keyvault.py` already takes a
  `tenant` arg) so the platform cannot bulk-decrypt one tenant's stored secrets.

- **P4 — Onboarding + tenant resolution.** Tenant onboarding/admin UI (create,
  activate, deactivate) on `/admin/tenants` (P0 ships it read-only). Resolve the
  request's tenant from subdomain and/or session — the `_resolve_tenant()` seam in
  `app.py` is the single place this lands; today it returns the session tenant or
  the `"default"` bootstrap and is only called when the switch is ON.

- **P5 — Posture.** SOC 2 Type II + ISO 27001/27017/27018, pen-test proving no
  cross-tenant leak, audit/DPA/breach-runbook for Art. 33/34, per-tenant data
  export/erasure (GDPR Art. 15/17). Gate go-multi-client on this.

---

### Cross-tenant test strategy

The non-negotiable acceptance bar for P2 (and the gate to flip the switch):

- **Every tenant-scoped read must be proven to return ONLY the current tenant's
  rows.** The harness seeds (at least) two tenants — A and B — with overlapping
  data, binds the context to A, runs the real query path, and asserts the result
  contains A's rows and **none** of B's. Then it flips to B and asserts the
  mirror. A bleed in either direction fails the test.
- `tests/test_tenancy.py::test_cross_tenant_scope_demo` is the **template** for
  this shape (it demonstrates the assertion against a throwaway table without
  touching any product query). Each P2 table change copies this shape against the
  real query.
- On Postgres, add a second harness layer that runs as the **non-owner app role**
  and asserts RLS blocks a deliberately unscoped query — proving the DB layer
  catches a forgotten `WHERE` (defense in depth).
- A cross-tenant leak found in CI is treated as a release blocker; a leak found in
  production is a GDPR Art. 33/34 reportable breach.

---

### Activation / rollback

- **Default:** `multitenant = "0"` (OFF). Single-tenant, inert, byte-identical to
  today. This is the only supported state until the gate below is met.
- **Gate to turn ON:** P1 (column + backfill) AND P2 (query scoping + a green
  cross-tenant test) complete for **every** tenant-scoped table touched by any
  reachable route, plus P3 connection/key enforcement on Postgres. Turning the
  switch on before then is unsupported and unsafe.
- **Rollback:** flip `multitenant` back to `"0"`. Because P1 only ADDS a column
  (the bootstrap tenant owns all existing rows) and P2's `scope_clause` is a
  no-op while OFF, turning the switch off restores single-tenant behavior without
  data migration. (Per-tenant encrypted secrets from P3 remain per-tenant; that is
  forward-compatible and needs no rollback.)

---

## Security & Compliance Evolution Plan — operating as a multi-client SaaS

**Purpose.** A concrete, ordered plan to evolve the platform into a multi-client SaaS **without
creating GDPR, antitrust, or security problems for the operator.** Grounded in the read-only
security audit (single-tenant baseline is strong; the product is *not* multi-client-ready yet).
Companion to `#multi-tenancy-program-plan` (the technical phasing) and `#deep-research-findings-logic-flaws-monetization` (which already
flags competition law as the #1 risk). This doc is the *operator's* compliance roadmap and the
**go-live gate**.

> **The one rule that prevents 90% of the legal risk:** do **not** turn the `multitenant` switch ON
> for real clients until every item in §1 ("Hard go-live gates") is done and proven. A single
> cross-tenant leak is a GDPR Art. 33/34 personal-data breach — reportable within 72 hours, with
> fines and trust damage. Until then the system is single-tenant and safe.

---

### 0. Current posture (where the line is today)

- **Safe now (single-tenant):** scrypt password hashing + per-user/per-IP lockout + timing-equalized
  verify; API tokens stored SHA-256 only, constant-time compare, default-off; envelope-encrypted
  portal credentials; central CSRF (fail-closed) + `_guard` on every route; CSP/HSTS/X-Frame;
  XML via ElementTree, zip-bomb + path-traversal guards; parameterized SQL throughout; setup wizard
  gated on zero-active-users.
- **Not safe for multi-client:** tenant isolation is a *foundation only* (nothing scoped), the worker
  carries no tenant, one KEK decrypts all tenants, no GDPR data-subject rights, the benchmark pools
  across clients, and one export has CSV formula injection.

**Implication:** the work ahead is *isolation + governance*, sequenced so you never onboard a second
client into a shared store that isn't yet partitioned.

---

### 1. Hard go-live gates — MUST be done before the 2nd client shares an installation

Each maps to an audit finding. None is optional; each is a breach or a real vulnerability if skipped.

| Gate | What | Why (risk if skipped) |
|---|---|---|
| **G1 — Scope every table** | Add `tenant_id` to every product + platform store (full list in the audit's inventory); wire `tenancy.scope_clause()` / Postgres RLS into **every** query reachable by any route; a green cross-tenant test per table. | Cross-tenant read/write = GDPR Art. 33/34 breach. |
| **G2 — Tenant-context the worker** | `tenant_id` on `intake_jobs`, stamped at enqueue from `current_tenant()`, `set_tenant()` in the worker before each register/close/fetch job. | Async jobs would write the wrong tenant's data even after the web path is scoped. |
| **G3 — Per-tenant credential custody** | Move off the `local` KEK to `env`/KMS with per-tenant `FFS_KEK_KEY_<TENANT>` (BYOK); run the re-wrap migration. | One key bulk-decrypts every tenant's supplier portal logins. |
| **G4 — Benchmark isolation** | Tenant-scope `benchmark.db` (`my_prices`/`wholesale_prices`) and the peer cohort; a client sees only its own entities. Keep any *pooled* benchmark OFF (see §3). | Antitrust hub-and-spoke information exchange between competitors. |
| **G5 — API tokens tenant-scoped** | `tenant_id` on `api_keys`; bind on `verify()`; scope every `/api/v1` query. | One client's token reads every client's customer master/IBANs. |
| **G6 — Platform stores scoped** | error_log, login_log, import_log, api_usage, audit CSVs get `tenant_id`; admin/log views never show another tenant's rows (PII in stack traces). | An admin "recent errors" view leaking another tenant's data is itself a breach. |
| **G7 — GDPR data-subject rights** | Per-tenant **export** (Art. 15/20) and **erasure/rectification** (Art. 17/16); a retention + auto-purge policy per data class. | Legally required of a controller/processor; no mechanism exists today. |
| **G8 — CSV/cell injection** *(do now — not multi-tenant-gated)* | Neutralize leading `= + - @ \t \r` in free-text cells of the accounting-ledger CSV and the xlsx exports. | Formula injection executes on the client's machine from an ingested supplier name/note. **Exploitable today.** |

G8 is being fixed immediately (it's a current vulnerability). G1–G7 are the multi-client gate.

---

### 2. GDPR framework (what you sign up to as operator)

- **Roles.** When you host clients' data you are typically a **processor** (Art. 28) acting on each
  client's instructions; the client is the **controller**. Each client needs a **DPA** with you, and
  you need a **sub-processor register** (Art. 28(2)/(4)) for: SharePoint/FTP backup target, AISP
  aggregator, factoring partner, AI backend (if enabled), and the KMS provider. List them, get the
  client's consent to each, and flow down the same obligations.
- **Records of processing (Art. 30).** Maintain a register: categories of data subjects (client staff,
  drivers via vehicle IDs), data categories (identity, IBAN, location-of-fuelling, credentials),
  purposes, recipients, retention, and transfers.
- **Data-subject rights (Art. 15–22).** Build per-tenant **access/export**, **erasure**,
  **rectification**. (G7.) Have a runbook to action a controller's forwarded request within a month.
- **Data minimisation & retention (Art. 5).** Define retention per class (transactions/VAT have legal
  tax minimums — keep; audit CSVs, error logs, data-lake AI artifacts — set a purge). Nothing
  auto-deletes today; add it.
- **Breach (Art. 33/34).** A 72-hour breach runbook; tenant-stamped logs so you can scope an incident
  to one tenant instead of declaring a breach for all. (G6.)
- **Residency / transfers (Ch. V).** Confirm the off-site backup and SharePoint/FTP target are in the
  EU/EEA (or covered by SCCs/adequacy). Encrypt snapshots at rest (they currently bundle `security.db`
  = password + API-key hashes + PII in error traces).
- **Security of processing (Art. 32).** Per-tenant encryption keys (G3), provable isolation (G1), audit
  on every write (G6) — the technical measures that make Art. 32 defensible.

### 3. Antitrust / competition framework (the benchmark is the trap)

- **The rule:** never let one client see another client's current/identifiable pricing — directly or via
  an aggregate small enough to reverse-engineer. That is the **hub-and-spoke information exchange** the
  EU 2023 Horizontal Guidelines treat as a competition-law violation.
- **Default posture (safe):** keep all benchmark/price intelligence **strictly intra-tenant** — a client
  benchmarks only its own entities/fuel cards. This is G4 and removes the risk entirely.
- **If you ever want a *pooled* cross-client benchmark as a product** (a real revenue idea, but
  counsel-gated): only via an **independent trustee/aggregation** model — minimum cohort of **distinct
  clients** (not entities), aggregation + **time-lag** so no current price is exposed, suppression when a
  cell could single out a contributor, and explicit legal sign-off + participant agreements. Do **not**
  build this on the live shared `benchmark.db`. Treat it as a separate, gated product (see
  `docs/STRATEGY.md` Opp. 3 / `#deep-research-findings-logic-flaws-monetization`).

### 4. The phased sequence (maps to #multi-tenancy-program-plan P0–P5)

- **P0 — Foundation** *(done)*: tenant registry + context + OFF-by-default switch + the plan. No client data
  touched.
- **P1 — Schema**: add `tenant_id` + backfill the existing single tenant on every store in the inventory
  (incl. platform stores G6 and `intake_jobs` G2). No behaviour change while OFF.
- **P2 — Enforce**: wire `scope_clause`/RLS into every query, table-by-table, **each landing with a green
  cross-tenant access test**; tenant-scope API keys (G5) and the benchmark/peer cohort (G4). This is the
  bulk of the work and the real isolation guarantee.
- **P3 — Custody**: per-tenant KEK/BYOK (G3) + re-wrap migration; encrypt backups, document residency (G9
  audit item); tenant-scoped backup isolation decision.
- **P4 — Rights & governance**: GDPR data-subject export/erasure + retention/auto-purge (G7); Art. 30/28
  registers, DPA template, 72-hour breach runbook; egress allow-list before tenant-admins configure
  portal/market URLs.
- **P5 — Assurance**: SOC 2 Type II + ISO 27001/27017/27018; pen-test of the tenant boundary; only then
  market multi-client / white-label.

**Activation rule:** the `multitenant` switch flips ON for a real second client only after **P1+P2 are
complete for every reachable table** (G1, G2, G4, G5, G6) and **P3 custody** (G3) is in place. P4 (rights)
must be live before or at the same time — it's a legal precondition, not a follow-up.

### 5. The operator decisions that shape the build (make these first)

1. **Isolation model:** shared-DB + Postgres RLS (app-level + DB-level defence in depth) for most clients,
   vs **DB-per-tenant** for a high-value client demanding hard isolation? (RLS footguns — non-owner role +
   FORCE RLS, transaction-local `set_config` — are documented in #multi-tenancy-program-plan.)
2. **Pooled benchmark:** intra-tenant only (safe default), or pursue the counsel-gated pooled product (§3)?
3. **Backup residency & isolation:** where do off-site backups physically live (EU?), and per-tenant or
   shared snapshots?
4. **Retention periods** per data class (tax/legal minimums vs GDPR minimisation).
5. **AI review in SaaS:** if offered, the AI backend becomes a named sub-processor and the
   "derived-data-only, never PDF/IBAN" guarantee needs contractual + test enforcement.
6. **KMS provider** for G3 (cloud KMS vs Vault) — also the credential-custody go-live decision.

### 6. Minimum compliant multi-client launch checklist

Single-client today is fine. To launch the *first shared multi-client install*, all of:
☐ G1 every table scoped + cross-tenant test green · ☐ G2 worker tenant-context · ☐ G3 per-tenant KEK ·
☐ G4 benchmark intra-tenant · ☐ G5 API tokens scoped · ☐ G6 platform stores + logs scoped ·
☐ G7 data-subject export/erasure + retention live · ☐ G8 CSV/cell injection fixed *(done)* ·
☐ encrypted backups + documented EU residency · ☐ DPA + Art. 30/28 registers + 72h breach runbook ·
☐ tenant-boundary pen-test.

---

#### One-paragraph version
The single-tenant product is secure; the multi-client version is not yet, because isolation is a
foundation only. Fix the one current-day vulnerability now (CSV injection, G8). Then treat the
`multitenant` switch as locked until you have scoped every table and the worker (G1–G2, G5–G6),
given each tenant its own encryption key (G3), kept the benchmark intra-tenant to avoid the antitrust
hub-and-spoke trap (G4), and built GDPR data-subject rights + retention + the Art. 28/30 governance
(G7, §2). Do that in the P1→P5 order, gate activation on a green cross-tenant test per table, and you
become a defensible processor rather than a breach waiting to happen.

---

### 7. Legal grounding (deep-research, 2025) — confirmations & corrections

A multi-source review (GDPR text, EDPB Guidelines 07/2020 & 01/2025, CNIL, ICO, EU 2023
Horizontal Guidelines, CJEU case-law, PostgreSQL docs, OWASP/CWE) **confirms the audit's
positions**, with these precision corrections to apply:

**GDPR — isolation, breach, rights, retention**
- ✅ A cross-tenant leak is a personal-data breach (Art. 4(12), confidentiality) → **72-hour**
  supervisory-authority notification (Art. 33). EDPB 01/2021 treats wrong-recipient disclosure as a
  notifiable breach.
- 🔧 **Art. 34** (notify the *individuals*) only triggers on **HIGH** risk and is **exempted where the
  data was encrypted** (Art. 34(3)(a)) — not automatic on any leak.
- 🔧 **Art. 32** is risk/outcome-based: it does not literally enumerate "tenant isolation," but
  isolation is *the* measure that delivers the required confidentiality/anti-unauthorised-access
  outcome in a shared store; encryption and **regular testing** (pen-test) are named.
- 🔧 **Data-subject rights:** in B2B SaaS the **client-tenant is the controller**; you (platform) are
  the **processor that *assists*** (Art. 28(3)(e)) — build per-tenant access/export/erasure
  *capability*; the one-month deadline (Art. 12(3)) is the client's duty.
- 🔧 **Retention:** GDPR mandates **defined retention + deletion at end-of-purpose** (Art. 5(1)(e)) and
  a documented policy — auto-purge is *recommended*, not black-letter. EU **VAT/tax law** lawfully
  requires multi-year retention of the *invoice/transaction records* (Art. 17(3)(b) overrides
  erasure) — but only those records; minimise/purge the rest.
- 🔧 **An IBAN is ordinary personal data, NOT Art. 9 "special category."** Its sensitivity is
  risk-based (fraud) and feeds the Art. 34 high-risk test. Vehicle/driver IDs **are** personal data in
  your hands (CJEU *Scania* C-319/22).

**GDPR — the owner-analytics crux (your "I must have all analytics" requirement)**
- Using clients' personal data for your **own** cross-tenant analytics/benchmark makes you a
  **CONTROLLER** for that use (**Art. 28(10)**) — needing your **own Art. 6 lawful basis** (legitimate
  interest + an LIA, or consent) **and transparency** to data subjects. Processor status doesn't cover it.
- **CNIL (2022):** a vague "product-improvement" DPA clause is **insufficient** — you need **specific,
  written client authorisation** + a case-by-case **Art. 6(4) compatibility assessment**.
- **The clean alternative:** **anonymise to the WP29/EDPB standard** (defeat singling-out + linkability
  + inference) → outside GDPR (Recital 26). **Caveat:** with only ~5 entities at station/route
  granularity, true anonymisation is hard; **pseudonymisation does NOT clear the bar** (EDPB 01/2025).
- **Decision tree for the owner view:** (a) build it on **aggregated/de-identified** data and keep PII
  (IBANs, driver/vehicle, contacts) out → lowest risk; **else** (b) be the **controller** for it with a
  documented LIA + specific DPA authorisation + transparency. Avoid the middle (vague clause over
  identifiable data).
- **Sub-processors** (SharePoint/FTPS, AI, KMS, AISP, factor): Art. 28(2)/(4) — prior authorisation,
  flow-down of terms, **you stay fully liable**; classify each (an AI vendor that sets its own purposes
  is a *controller*, not a sub-processor). Maintain **dual Art. 30 records** (processor + own-controller).
- **Non-EEA backups** = a Chapter-V transfer → SCCs + a transfer-impact assessment + **exporter-held
  encryption** (your keyvault/BYOK design is the Schrems-II supplementary measure).

**Antitrust — the platform-as-hub trap (why the benchmark stays intra-tenant)**
- A **platform can be the "hub"** of an unlawful information exchange (CJEU **Eturas** C-74/14; the 2023
  Guidelines name online platforms as hubs), and a **facilitator that isn't even a competitor is liable**
  (CJEU **AC-Treuhand**) — "we don't sell fuel" is **no defence**.
- A client who merely **sees** the shared output is **presumed party** to a concerted practice unless it
  **publicly distances** itself (Eturas) — so feeding competitor-derived insight back to clients is the
  danger.
- Exchanging individualised **future** prices/quantities is a restriction **by object**; the 2023
  Guidelines **widened** this to *anything that removes strategic uncertainty* (CJEU **Dole** — even
  "trend" data). **There is NO safe harbour**; the old US "≥5 contributors / ≤25% share" rule was
  **withdrawn in 2023** (heuristic only).
- ✅ **Therefore: keep `benchmark.db` strictly intra-tenant** (a client benchmarks only its own
  entities). A *pooled* product is lawful only if: **aggregate-only, non-attributable** output; each
  client sees **only its own data + the aggregate**; an **independent trustee**; a **cohort minimum +
  dominance cap + cell-suppression** against re-identification; **time-lagged historic** data; and
  **counsel sign-off** — never individualised current/future prices.
- Your internal **owner analytics** (holding/processing for your own business) is permissible; the line
  is **never relaying** one client's current/identifiable pricing to another (directly or via a
  reverse-engineerable aggregate).

**Technical — refinements**
- 🔧 **RLS leak vectors (corrected):** an RLS-*enabled* table with no policy fails **closed**
  (default-deny). The real leaks are: RLS **never enabled**; the app connects as the **table owner
  without `FORCE ROW LEVEL SECURITY`**; the app role has **BYPASSRLS**; or **views owned by a privileged
  role** bypass RLS (use `security_invoker` views, PG15+). The "non-owner role + FORCE RLS +
  transaction-local `set_config`" rule stands.
- ✅ **One KEK bulk-decrypts all tenants** — per-tenant keys/BYOK fix it; bonus: **crypto-shredding**
  (destroy a tenant's key) satisfies **Art. 17 erasure** without hunting every copy.
- ✅ **CSV formula injection (CWE-1236)** — the apostrophe-prefix (shipped) is the OWASP **baseline** but
  is **Excel-save/reopen-fragile**, and you must also escape separators/quotes (Python's `csv.writer`
  already does the latter). Sound as the standard mitigation; note the Excel limitation.

**Key sources:** GDPR Arts. 4(12), 5, 6(4), 12, 15/17/20, 28, 30, 32–34, 44–46, Recital 26; EDPB
Guidelines 07/2020 (controller/processor), 01/2021 (breach examples), 09/2022 (breach notification),
01/2025 (pseudonymisation); CNIL 2022 processor-reuse guidance; EU Horizontal Guidelines 2023/C 259/01
(Ch. 6 information exchange); CJEU *Eturas* C-74/14, *AC-Treuhand* C-194/14 P, *Dole* C-286/13 P,
*Scania* C-319/22; PostgreSQL Row-Security docs; OWASP CSV Injection / MITRE CWE-1236.
*(Grounded research guidance, not a legal opinion — get counsel sign-off before going multi-client
and before any pooled-benchmark product.)*

---

## 10. Document-Management Core + Secure-Sharing platform (program record, June 2026)

Records the document-platform program: the decision to build a **document-management core with
switchable modules**, the competitor analysis that shaped it, the architecture guardrails, and
what shipped.

### 10.1 Product framing — core + modules; single-tenant now / multi-client later
Read the product as a **financial-data + document-management CORE with delegated modules switched
on per deployment** (and, later, per client). Decision: **build single-tenant now, keep every new
module multi-tenant-ready** so flipping to multi-client SaaS is incremental, not a rewrite.

Guardrails that keep "single now, multi later" cheap (every new module follows them):
1. Route client-data access through the `tenancy.py` seam (`scope_clause()`/`require_tenant()` —
   inert today, wired in now); every new table carries `tenant_id` (stamped via `tenancy.write_tenant()`).
2. Module on/off stored so it can gain a per-tenant dimension later.
3. Secrets per-tenant-capable (`keyvault` BYOK).
4. The **data-product boundary** holds: the ENGINE writes the product DBs; the app reads them
   READ-ONLY; every new feature keeps its own state in an **app-owned overlay DB** keyed by the
   `doc:<id>` reference — never a column on a product DB.

### 10.2 Competitor analysis (the field, June 2026)
Researched against the open-source DMS field + Papermark (cloned + Prisma-schema-scanned).

- **Papermark** (AGPL-3.0 core; `ee/` features commercially licensed) — a DocSend alternative:
  secure *sharing* + page-by-page analytics + data rooms. Stack: Next.js/Postgres/Prisma/Tinybird/
  pdf.js. It is an **outbound** layer, not a storage repository; its data model
  (Document/DocumentPage/Link/View/Viewer/Dataroom/Agreement/Conversation/Chat) was the blueprint
  for our sharing module.
- **DMS field** — Mayan EDMS & Paperless-ngx (Python/Django; **Mayan Apache-2.0 = the only one safe
  to borrow CODE from**; Paperless GPL-3.0 = design-only), OpenKM/LogicalDOC (Java; e-sign/zonal-OCR/
  watermark/retention paywalled), Alfresco/Nuxeo (heavy ECM), Teedy/SeedDMS (lightweight).
- **Table-stakes taxonomy** a serious DMS is judged on: OCR, full-text search, metadata/tagging,
  versioning, granular access control, workflow, audit, REST API, retention/compliance, and — new
  for 2026 — AI. (Corroborated by EDMSNext + every 2026 roundup.)
- **Licensing guardrail:** borrow CODE only from Mayan (Apache-2.0); design-only from Papermark/
  Paperless/OpenKM. We built native — did NOT adopt the Next.js/Postgres stack.

### 10.3 Architecture decisions
- **Backend stays Python/Flask** (Mayan & Paperless prove a full DMS in Python; OCR/search/AI are
  Python-native; rewriting a deployed finance engine = pure risk for no gain).
- **Frontend = a light JS layer** (vanilla JS + self-hosted pdf.js as `static/` assets under the
  existing `script-src 'self'` CSP; no inline scripts, no framework, no second stack). The
  page-by-page viewer is browser-side pdf.js emitting beacons — the one genuinely client-side piece.
- Each module is an **app-owned overlay** (own SQLite DB, `db_migrate`-versioned, `audit`-installed,
  tenancy-stamped), keyed by `doc:<id>`.

### 10.4 What shipped (all reviewed + tested — suite at 1301 passing, `consolidate.py` green)

Document-management core:
- **A1 — Capture/OCR:** generic on-prem heuristic extractor (an unrecognised invoice auto-prefills
  its header instead of showing an empty form — deterministic, no AI, no bytes leave the box) +
  multilingual Tesseract OCR (`EXTRACT_OCR_LANGS`).
- **A2 — Full-text search:** SQLite FTS5 over the document/invoice corpus (read-only from the
  product DBs into an app-owned index); search page + admin rebuild + monthly-close hook.
- **A3 — Metadata:** typed custom fields (text/number/monetary/date/boolean/select/documentlink) +
  hierarchical tags (cycle-guarded); folded into search.
- **A4 — Versioning:** append-only version chains per document; bytes via the vault's own store API;
  revert records a new version and never destroys history.
- **A5 — Retention + legal hold:** advisory records-management (GDPR/ISO-27001) — flags records past
  retention for human review, legal hold overrides retention, longest-retention-wins, fully audited;
  **never auto-deletes.**

Secure-sharing module (Papermark-style, native):
- **B1 — Secure share links:** trackable public `/s/<token>` link over a vaulted document;
  expiry/password/require-email gates; view log + owner alert; enumeration-safe.
- **B2 — Access controls:** NDA/agreement gate (acceptance logged) + dynamic watermark (pypdf overlay).
- **B3 — Page-by-page analytics:** self-hosted pdf.js viewer (the `static/` JS layer) + per-page
  dwell beacons; engagement dashboard; CSP scoped to the viewer response only (global stays strict).
- **B4 — Data rooms:** branded multi-document rooms with folders, per-room gates, and a Q&A module.
- **B5 — AI document assistant:** opt-in, default-OFF, advisory-only chat over **derived data only**
  (the `ai_review` redactor strips IBAN/secrets; never the PDF), never mutates a figure.

Follow-on (shipped same session):
- **① Document automation hub:** generate-and-vault for the existing template/contract generator
  (generated contracts/POAs become first-class vaulted docs → tags/versions/sharing/e-sign) + a
  cross-customer document-requests **control board**.
- **② E-signature (SES):** public signing flow on the share-link gate (consent + typed/drawn
  signature) → signed PDF + certificate page + **SHA-256 integrity binding + verify**; internal
  "send for signature"; attaches a signed contract back to its request. Labelled SES (not eIDAS QES).
- **③ Deepen sharing:** per-recipient document permissions in data rooms (fail-closed), per-owner
  email alerts (relay fallback), and custom **branding** for the public viewer (same-origin logo only).
- **④ Multi-tenancy phase 2 (new modules):** `scope_clause` read-isolation wired into sharing/esign/
  metadata/versioning/retention/search; public links bind their own tenant; OFF stays byte-identical.

### 10.4a What shipped — the AI, MCP, workflow & export round (same program, later session)
Builds on 10.4 and keeps every guardrail (10.1/10.3): each new feature is an app-owned overlay
(own gitignored DB, `db_migrate`-versioned, `audit`-installed, tenancy-stamped), advisory, and
default-OFF where it touches an external AI. The AI-capture path is the deliberate, loudly-gated
exception to the deterministic-first capture rule (it sends PDF page-images to the configured
provider) — a human still confirms every figure.

- **AI capture pipeline (opt-in, default-OFF, advisory):**
  - **Vision capture** (`vision_capture.py`) — reads a scanned/messy/unknown-layout PDF's page
    images with a vision model (Claude/OpenAI) into a structured "capture document" mapped into the
    SAME review-draft + confirm gate; strict (never invents a field), best-effort (falls back to the
    OCR→parser→text-AI chain). Inert unless `ai_vision_capture_enabled` AND a vision backend.
  - **AI verify + correct** (`ai_verify.py`) — an INDEPENDENT verify model/provider verifies the
    captured draft field-by-field against the PDF (the PDF is authoritative), can APPLY
    PDF-authoritative corrections and re-verify, with status/test-connection visibility. Advisory:
    it flags/corrects for the human, never auto-gates a figure. Inert unless `ai_verify_enabled` AND
    a vision backend.
  - **Capture as a second file** (`capture_file.py`) — persists the capture document as a permanent
    JSON + human-readable text file in the data lake (kind `capture_document`), linked to the upload
    SHA-256, re-saved after corrections (newest-wins); never the PDF/secret bytes.
  - **Capture-confidence learning loop** (`capture_confidence.py`, own `capture_confidence.db`) — a
    per-(supplier × field) accuracy ledger fed by the AI-correction + human-edit-at-confirm signals;
    advisory hints that flag a supplier's weak fields on the review screen. NEVER gates.
- **Classification / DLP** (`classify.py`, own `classify.db`) — a Box-Shield-style overlay that scans
  document text for sensitive types (IBAN/account/SWIFT/card/email/phone/VAT-id/name-ish), stores
  ONLY `{type, count}` + an ordered sensitivity label (never raw values), and (OPT-IN) gates what
  may be sent to external AI by sensitivity. Default `ai_external_max_sensitivity=restricted` =
  permissive (byte-identical); fails OPEN on a scan error, CLOSED when a policy is set and exceeded.
- **MCP server** (`mcp_tools.py` + `mcp_server.py`, `docs/MCP.md`) — a READ-ONLY, token-gated,
  tenant-aware Model-Context-Protocol server (modeled on Box's) exposing platform data (search,
  reclaimable VAT, claim status, benchmark, KPIs, document metadata, overdue requests) to AI agents.
  v1 has no write/action tools; it reads product DBs strictly via `dataproduct` (ro), never returns
  bank/secret fields, and never raises. The `mcp` SDK is an OPTIONAL extra (`requirements-mcp.txt`)
  imported only inside the server — the app/tests never need it. stdio is trusted; streamable-HTTP
  requires a bearer token (`FFS_MCP_TOKEN` or an `api_keys` token).
- **Workflow engine** (`workflow.py`, own `workflow.db`) — a Box-Relay-style configurable, ordered
  approval/routing engine (steps: approve/sign/notify/tag) with a Tasks/Approvals inbox. Advisory:
  structurally separate from the refund engine, it NEVER overrides a VAT legal gate; a run reaching
  `approved` changes nothing about a claim. Side-effect steps reuse `esign`/`notify`/`metadata`.
- **E-invoice / ERP export** (`einvoice_export.py`) — the outbound counterpart to
  `extract.parse_einvoice`: EN-16931 / UBL 2.1 Invoice XML export of registered invoices (single +
  batch ZIP), gathered with the existing CSV/SAF-T exports into an "Accounting & ERP exports" hub.
  Read-only over `vat_refund.invoice_lines`, NET-EUR via `money.f2`; invents no figure.
- **Embedded-finance origination, deepened** (`finance.py`) — beyond the financeable-total view, a
  per-claim advance/fee model (`financeable_offers`/`offer_for`: advance, fee, net-now, net-later,
  expected payout) and an advances ledger (own `finance.db`) tracking {offered, accepted, funded,
  repaid, declined}. NullProvider default — nothing funds, no money moves, no VAT figure mutates.
- **Advisory document chat** (`ai_assistant.py`, own `ai_chat.db`) — the conversational sibling of
  `ai_review.py`: opt-in/default-OFF Q&A over a document's DERIVED data only (the `ai_review`
  redactor strips IBAN/secrets; never the PDF), never mutates a figure.
- **Navigation IA tidied** — top-level nav consolidated to Home · Intake · Documents · Sharing ·
  Analytics · VAT & Recovery · Master data · History · Export · Admin.
- **Test-suite reliability** — a conftest fixture isolates `app_settings` + `role_permissions` per
  test, making the suite deterministically green regardless of order.

### 10.5 Backlog (still open — deepen further)
- **Real per-supplier capture/scraper adapters + live API/e-invoicing inbound** — the AI-capture and
  fetch scaffolding has landed; the flagship remaining work is the concrete adapters and KMS/OAuth/
  per-tenant-BYOK credential custody beyond the `env`/local KEK seam.
- **MCP write/action tools** (v2) — v1 is read-only by design; any action surface needs a fresh
  authz/audit review before it can mutate.
- **Custom domains** for the public viewer (needs nginx/per-tenant cert work — server-side).
- Multilingual OCR **per-language tuning** + scanned-only language autodetect (the code passes
  `EXTRACT_OCR_LANGS`; today defaults to the Baltic/EU set).
- **eIDAS-qualified** e-signature (the shipped SES is simple-electronic; QES needs a TSP/certificates).
- **PK-rekey slice** for cross-tenant natural-key UNIQUE indexes (e.g. `doc_versions(subject_ref,
  version_no)`, metadata `(field_id, subject_ref)`) — the same follow-on the legacy harnesses note;
  and running `search.rebuild` under owner-scope so metadata text is indexed under multitenant.
- Optional **CMIS/WebDAV** interop surface if a client needs ERP/DMS integration (defer behind `/api`).
- **Validated per-country SAF-T / e-invoice profiles** — the EN-16931/UBL export and SAF-T core are
  in; per-country submission profiles still need validation against each authority's schematron.
