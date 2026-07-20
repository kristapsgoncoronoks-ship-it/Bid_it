# InvoiceIQ — Product Requirements Document

> **Status:** Draft v1 · Owner: Product · Last updated: 2026-07-20
> **Companion docs:** [personas](./personas.md) · [workflows](./workflows.md) · [pricing](./pricing-hypothesis.md) · [metrics](./metrics.md) · [risks](./risks.md)
>
> This PRD converts the product context into an **implementable and sellable** definition. It is deliberately opinionated about cutting scope. Legend for build status against the current codebase: ✅ built · 🟡 partial · ⬜ not built.

---

## 1. Executive summary

InvoiceIQ turns a company's messy, multi-supplier invoice and expense flow into a **clean, VAT-aware, audit-ready financial record** — captured automatically, categorised, checked for errors, and exportable to accounting systems and structured e-invoice formats. It serves European SMEs and the accountants who serve them, across multiple legal entities and currencies.

The product context describes an extremely broad surface (AP capture, AR invoicing, expense management, dashboards, VAT, exports, API platform, embedded finance). **That is three or four products.** This PRD picks one wedge to sell first and sequences the rest.

### The wedge (what we sell first)

> **"Get every supplier invoice — across every channel — into one clean, VAT-correct, exportable record, with the errors flagged before they cost you."**

This is a *data-quality + compliance* job, not a full ERP. It is defensible because the moat is the **proprietary, multi-channel, line-item invoice dataset** and the trust that comes from getting VAT and duplicates right. AR invoice **issuing** (already built, EN-16931/Factur-X) is a strong attached module, not the wedge.

### Scope challenge (explicit cuts)

| Area | Decision | Rationale |
|---|---|---|
| Employee expense management | **Post-MVP module**, not in the sellable wedge | Competing head-on with Pleo/Payhawk/Spendesk needs cards + banking; we have the reporting shell (✅) but not the category-defining feature. Keep as attach. |
| Embedded finance / factoring the VAT receivable | **Excluded from v1** (Later) | Requires a licensed partner, credit risk, and regulatory surface we should not carry pre-PMF. |
| Open-banking reconciliation / pay-by-bank | **Excluded from v1** (Later) | Needs a licensed AISP/PISP partner (PSD2). Advisory recon only, if at all. |
| Public/pooled benchmark of prices | **Excluded from v1** (Later) | Counsel-gated (competition law); internal-only benchmark is fine. |
| Full AR billing suite (dunning automation, payment collection) | **Trim to essentials** | Issue + PDF + reminders + credit notes + recurring (✅) is enough; do not build a Stripe Billing competitor. |
| "Everything for everyone" role matrix | **Keep but cap** | Four company roles + platform operator (✅) is enough; do not model 7 bespoke role types in v1. |

---

## 2. Primary customer segments

Ranked by *fit for the wedge* (ease to sell v1), not by TAM.

1. **Accountancy & bookkeeping practices (2–50 staff)** — *beachhead.* They process invoices for many client companies, feel the pain acutely, and buy tools that save billable hours. One practice = many tenants. Highest willingness to pay per seat, strongest referral loop.
2. **Multi-entity SMEs (10–250 employees)** — transport/logistics, property management, construction, professional services. Multiple fuel cards / suppliers / legal entities; today they reconcile in spreadsheets. Strong fit for dimensions (vehicle/property/project) and VAT-across-entities.
3. **Finance-team-of-one SMEs (5–50 employees)** — a single bookkeeper/office manager drowning in PDFs and email attachments. Buys for time saved and "no more missed invoices."

**De-prioritised for v1:** micro/sole-traders (low ACV, served by Xero/QuickBooks natively) and large enterprises (long sales cycle, need SSO/SOC 2/DPA we don't have yet — Enterprise scope later).

---

## 3. Jobs to be done (summary)

Full table in [workflows.md](./workflows.md#jobs-to-be-done). The load-bearing jobs for the wedge:

- **Capture** every incoming invoice regardless of format (PDF, scan, XML e-invoice, CSV, email attachment) without manual re-keying.
- **Trust** the numbers — catch duplicates, missing fields, tax inconsistencies, and unusual prices *before* they hit the books.
- **Classify** spend by supplier, tax code, cost centre, department, project, vehicle, property.
- **Reconcile** a period to a clean, complete record.
- **Export** to the accounting system / e-invoice format / VAT return without re-work.
- **(Attach) Issue** compliant customer invoices, credit notes, and recurring bills.

---

## 4. Pain points & current alternatives

| Pain | Today's alternative | Why it fails | Our answer |
|---|---|---|---|
| Invoices arrive in 6 formats across email, portals, post | Manual download + re-key into accounting SW | Slow, error-prone, no single record | Multi-channel capture → one normalised record (✅ upload/CSV/XML, 🟡 email intake, ⬜ portal capture) |
| Duplicate payments / missed invoices | Eyeballing + luck | Real money lost; found months later | Duplicate + missing-field + anomaly detection (🟡 validation engine) |
| VAT wrong across currencies/entities | Spreadsheets + accountant clean-up | Costly, error-prone, audit risk | ECB-FX + VAT scheme handling + per-entity (✅ FX/VAT, ✅ multi-entity issuer) |
| No visibility into where money goes | Quarterly management accounts | Too late to act | Live spend dashboards + dimension breakdowns (✅) |
| Accountant re-keys client data | Email ping-pong + shared drives | Non-billable hours, version chaos | Multi-tenant workspace + exports + audit trail (✅ tenant isolation, ✅ audit) |
| e-invoicing mandates coming (ViDA) | Not ready | Compliance cliff 2028–2030 | EN-16931 / Factur-X in + out (✅ parse + issue) |

Direct competitors to be aware of (positioning, not feature parity): Dext/Receipt Bank, AutoEntry, Klippa, Rossum (capture); Xero/QuickBooks/Sage (accounting); Pleo/Payhawk (expense+cards); Basware/Tipalti (enterprise AP). **Our lane: multi-channel capture + EU VAT/e-invoice correctness for SMEs and their accountants — lighter than enterprise AP, more compliance-serious than a receipt scanner.**

---

## 5. Functional requirements

Grouped by capability. Each line tagged with MoSCoW for **v1 (the sellable wedge)** and current build status.

### A. Capture & intake
- **F-A1** Upload PDF/image/CSV/XML and parse to a reviewable draft — **Must** ✅
- **F-A2** Structured e-invoice (UBL/CII, Factur-X/ZUGFeRD) deterministic parse — **Must** ✅
- **F-A3** OCR fallback for scanned PDFs — **Must** 🟡
- **F-A4** Email-in intake (dedicated address; attachments → review queue) — **Should** 🟡
- **F-A5** API ingest endpoint (token-gated) for automation/n8n — **Should** 🟡
- **F-A6** Supplier-portal credentialed capture — **Later** ⬜ (biggest build; needs credential vault ✅ scaffold + per-supplier adapters)

### B. Review, validation & approval
- **F-B1** Human review/confirm of every parsed draft — **Must** ✅
- **F-B2** Duplicate detection (same supplier + number + amount) — **Must** 🟡
- **F-B3** Missing-field / tax-inconsistency / date checks — **Must** 🟡
- **F-B4** Anomaly / unusual-price flags — **Should** 🟡
- **F-B5** Configurable approval routing (thresholds, approvers) — **Could** ⬜
- **F-B6** Advisory AI validation (opt-in, never blocks, derived data only) — **Could** 🟡

### C. Classification & analytics
- **F-C1** Categorise by supplier, tax code, category — **Must** ✅
- **F-C2** Cost-allocation dimensions: cost centre, department, project, vehicle, property — **Must** ✅
- **F-C3** Spend dashboards (over time, top vendors, by category/status/dimension) — **Must** ✅
- **F-C4** VAT summary + aging + cash-flow views — **Should** ✅/🟡
- **F-C5** Explore / ad-hoc query surface — **Could** ✅

### D. VAT, FX & multi-entity
- **F-D1** ECB reference-rate FX conversion to EUR with provenance — **Must** ✅
- **F-D2** VAT scheme handling (standard, reverse-charge, intra-EU, exempt) — **Must** ✅
- **F-D3** Multiple legal entities / issuers per tenant — **Must** ✅
- **F-D4** Per-country VAT registration handling — **Should** 🟡
- **F-D5** VAT-refund workflow (Dir. 2008/9/EC) — **Later** ⬜ (lives in sibling product; do not fold into v1)

### E. Outbound invoicing (attached module)
- **F-E1** Issue EN-16931 invoice (PDF + embedded Factur-X XML) — **Should** ✅
- **F-E2** Credit notes (linked, own series) — **Should** ✅
- **F-E3** Recurring invoice schedules — **Should** ✅
- **F-E4** Payment tracking (paid/partial/overdue/credited) + reminders — **Should** ✅
- **F-E5** UBL/e-invoice + ERP export (DATEV/Xero/QuickBooks) — **Should** 🟡

### F. Export & integration
- **F-F1** CSV / Excel export of transactions & reports — **Must** ✅
- **F-F2** Accounting-ledger / SAF-T export — **Should** 🟡
- **F-F3** Outbound webhooks (signed, retrying) — **Should** ✅
- **F-F4** Native accounting-package sync (2-way) — **Later** ⬜

### G. Platform, security & admin
- **F-G1** Multi-tenant isolation with defence-in-depth (row + ORM guard) — **Must** ✅
- **F-G2** Invitation-only workspace join; unique per-registration org id — **Must** ✅
- **F-G3** Role-based access (user/admin/owner + platform operator) — **Must** ✅
- **F-G4** Immutable, hash-chained audit log — **Must** ✅
- **F-G5** Durable background job queue + worker — **Must** ✅
- **F-G6** Usage metering + per-plan limits — **Must** ✅
- **F-G7** Subscription plans + module gating — **Must** ✅ (billing provider ⬜)
- **F-G8** SSO / SAML / SCIM — **Later (Enterprise)** ⬜
- **F-G9** Configurable data retention + legal hold — **Should** ⬜ (compliance-critical, see §9)

---

## 6. Non-functional requirements

| # | Requirement | Target |
|---|---|---|
| NFR-1 | **Tenant isolation** | Zero cross-tenant read/write; enforced at row + ORM layer; every new table registered in tenant scope. A cross-tenant leak is a P0/GDPR breach. |
| NFR-2 | **Money correctness** | All amounts Decimal, ROUND_HALF_UP; no float arithmetic on currency; VAT + FX auditable to source. |
| NFR-3 | **Durability** | Financial documents and audit trail never silently lost; background work idempotent + retried + dead-lettered. |
| NFR-4 | **Availability** | 99.9% for the app; capture queue degrades gracefully (queued, not dropped) during incidents. |
| NFR-5 | **Performance** | Dashboard queries < 1.5s p95 at 100k invoices/tenant; upload→draft < 10s p95 (excl. heavy OCR). |
| NFR-6 | **Security** | Uploads scanned + type-validated at a single choke point; secrets envelope-encrypted; strict CSP; documents served inert. |
| NFR-7 | **Auditability** | Every data change attributed to an actor; append-only audit chain verifiable. |
| NFR-8 | **Data residency** | EU-region hosting; no personal data leaves the EU without SCCs/adequacy (see §10). |
| NFR-9 | **Observability** | Structured logs, request IDs, metrics (Prometheus), health/readiness probes. |
| NFR-10 | **Accessibility & responsive** | WCAG 2.1 AA target for core flows; usable on tablet/laptop. |
| NFR-11 | **Recoverability** | Point-in-time restore; verified backups with integrity manifest. |
| NFR-12 | **Scalability path** | Modular monolith now; Postgres; worker tier scales independently before any microservice split. |

---

## 7. Product boundaries

**InvoiceIQ is:** a system of record and intelligence for invoices and spend — capture, validate, classify, analyse, issue, export.

**InvoiceIQ is not (v1):**
- an accounting/GL system (we export *to* Xero/QuickBooks/DATEV, we don't replace them);
- a payments processor or money-mover (no funds ever touch our rails);
- a corporate-card / spend-control product;
- a lender or factoring provider;
- a tax filing agent (we produce VAT-ready data; a human/accountant files);
- a full BI tool (we ship opinionated dashboards, not a query builder for arbitrary datasets).

### Explicitly excluded features (v1)
1. Card issuing / spend controls.
2. Embedded lending / VAT-receivable factoring.
3. Open-banking payment initiation (PISP) and account aggregation (AISP).
4. Public/pooled cross-customer benchmark.
5. Automated tax **filing** to authorities.
6. Two-way native ERP sync (export only in v1).
7. Supplier-portal scraping at scale (scaffold only; real adapters are Later).
8. On-prem / self-hosted deployment.

---

## 8. Scope tiers

### MVP (sellable v1) — "Capture, trust, classify, export"
Capture (F-A1/2/3), review + core validation (F-B1/2/3), classification + dashboards + dimensions (F-C1/2/3), FX + VAT + multi-entity (F-D1/2/3), CSV/Excel export (F-F1), platform + security + metering + plans (all F-G Must). Outbound issuing (F-E1/2/3/4) shipped as an **attach module** because it's already built and differentiates — but the MVP *sale* stands on AP capture + analytics.

### Post-MVP
Email intake GA + API ingest (F-A4/5), anomaly detection (F-B4), approval routing (F-B5), SAF-T/ERP export (F-F2, F-E5), retention + legal hold (F-G9), employee expenses module GA.

### Enterprise
SSO/SAML/SCIM (F-G8), configurable data residency, custom retention & legal hold, DPA + SOC 2 Type II + ISO 27001, audit exports, priority SLA, sandbox, higher/again-custom limits, dedicated support, 2-way ERP sync, portal-capture adapters.

---

## 9. Legal, compliance, residency & retention assumptions

> These are **assumptions to validate with counsel**, not legal advice. See open questions in §11 and [risks.md](./risks.md).

### Legal / compliance
- **GDPR applies.** We are a **data processor** for customer/business data our tenants upload, and a **data controller** for account/usage data. We need: a **DPA** offered to every customer, an **Art. 30 record of processing**, a **breach process (Art. 33/34, 72h)**, **DSAR** handling, and sub-processor transparency.
- **PII minimisation.** Invoices and expenses contain personal data (names, emails, bank references, employee expense detail). Collect only what the job needs; never log secrets/IBANs; mask bank data in analytics.
- **Not a regulated financial entity in v1.** By never moving money we stay outside PSD2/e-money licensing. Any future banking/lending feature goes through a **licensed partner**, not us.
- **e-invoicing / ViDA.** EU "VAT in the Digital Age" phases in mandatory structured e-invoicing and digital reporting (2028–2030, member-state timelines vary). Our EN-16931/UBL/Factur-X support is the on-ramp; treat national formats (FatturaPA, Factur-X, KSeF, etc.) as a post-MVP expansion matrix.
- **Authenticity & integrity of invoices** (Dir. 2006/112/EC Art. 233): stored invoices must preserve authenticity of origin, integrity of content, and legibility for the whole retention period. Our SHA-256 hashing + immutable audit chain + inert original-document vault support this claim; get it reviewed.

### Data residency assumptions
- **Default region: EU.** Host application, database, object storage, and backups in an EU region (e.g. `eu-central-1` / `eu-west-1`).
- **No US transfer by default.** Any sub-processor outside the EU/EEA requires an adequacy decision or SCCs, documented in the sub-processor list. AI/OCR vendors are the main risk — prefer EU-hosted models, and the AI pipeline stays **opt-in, default-off, derived-data-only** where external.
- **Enterprise:** offer region pinning and a documented residency guarantee.

### Financial-document retention assumptions
- **Invoices are legal records with statutory retention.** Retention periods vary by member state and document type — commonly **7 years**, up to **10 years** (e.g. property-related in some states) and **10 years for company books** in DE/AT/FR. UK VAT records ~6 years.
- **Assumption for v1:** default retention **10 years**, tenant-configurable per legal requirement, with **legal hold** (suspends deletion) and immutability during the window. Deletion (incl. GDPR erasure) must **respect statutory retention** — erasure of a legally-retained invoice is refused/deferred, and that conflict is surfaced, not silently resolved.
- Retention config + legal hold (F-G9) is **Should for MVP-adjacent** because selling to accountants/regulated SMEs will surface it fast.

---

## 10. Prioritised feature list (Must / Should / Could / Later)

Consolidated from §5. This is the build/sell order.

**Must (v1 cannot ship or sell without these)**
Multi-channel capture (upload/CSV/XML + OCR) · human review · duplicate + missing-field + tax checks · classify + dimensions · spend dashboards · FX + VAT + multi-entity · CSV/Excel export · tenant isolation · invitation-only join · roles · audit log · job queue · usage metering + plan limits · plans + module gating (billing provider wiring) · EU hosting.

**Should (fast-follow; needed to expand the deal / land accountants)**
Email-in intake GA · API ingest · anomaly detection · outbound issuing module (issue + credit notes + recurring + reminders) · SAF-T/ledger + ERP export · outbound webhooks · per-country VAT · retention + legal hold · accountant multi-client console.

**Could (differentiators, not blockers)**
Approval routing · advisory AI validation/capture (opt-in) · explore/ad-hoc · document management overlays (search/metadata/versioning) · e-sign/sharing.

**Later (needs partner, licence, or scale)**
Supplier-portal capture adapters · SSO/SAML/SCIM · configurable residency · SOC 2/ISO · embedded finance · open-banking recon/pay · public benchmark · tax filing · 2-way ERP sync · self-host.

---

## 11. MVP acceptance criteria

MVP is **done and sellable** when a new tenant can, unaided, complete the core loop and a stranger's data is provably invisible. Each criterion is testable.

**Onboarding & tenancy**
- [ ] A new user can self-register, creating a workspace with a **unique org id**; they become the workspace **owner** (never a system admin).
- [ ] A second registration using the **same company name** creates a **separate, isolated** workspace — no shared data.
- [ ] A user who was **not invited** sees **zero** rows of any other workspace (verified by an automated cross-tenant isolation test in CI).
- [ ] Invited users join only via invitation; roles are enforced centrally.

**Capture → record**
- [ ] Uploading a PDF, a CSV, and an XML e-invoice each produces a **reviewable draft** the user confirms into a saved invoice.
- [ ] A scanned PDF with no text layer is parsed via OCR fallback (or clearly flagged as needing manual entry).
- [ ] Confirmed invoices form one normalised record with supplier, dates, currency, subtotal/VAT/total, and EUR conversion with FX provenance.

**Trust**
- [ ] Re-submitting the same supplier+number+amount raises a **duplicate** flag before save.
- [ ] Missing mandatory fields and an obviously inconsistent tax total are flagged.

**Classify & analyse**
- [ ] Any invoice can be tagged with cost centre / department / project / vehicle / property; tags are editable and clearable.
- [ ] Dashboards show spend over time, top vendors, by category/status, and **by dimension**; a dimension breakdown **sums to total spend**.

**VAT / FX / multi-entity**
- [ ] A non-EUR invoice converts at the ECB rate for its date, with source recorded.
- [ ] Reverse-charge / intra-EU / exempt schemes compute VAT correctly and state the legal note.
- [ ] A tenant can hold ≥2 legal entities, each with its own registration details.

**Export**
- [ ] Transactions and each report export to CSV/Excel with a stated NET/EUR basis.

**Attach: issuing (if module on)**
- [ ] Issue an EN-16931 PDF invoice with embedded valid Factur-X XML; number series is gap-free per entity.
- [ ] Credit a full or partial invoice; a credit note reduces outstanding and turnover; over-crediting is refused.
- [ ] A recurring schedule generates the correct invoices with no duplicates on re-run.

**Platform & trust**
- [ ] Every data change appears in an immutable, hash-chained audit log the owner can verify.
- [ ] Background jobs retry with backoff and dead-letter; nothing is silently lost.
- [ ] A plan's monthly document/upload limit is enforced (402 at the cap) and shown to the user.
- [ ] Data is hosted in an EU region; a DPA is available to sign.

**Quality gates**
- [ ] Automated test suite green in CI (incl. tenant-isolation + money-precision tests); migrations run clean from empty DB.
- [ ] No secrets in logs; uploads scanned + type-validated; documents served inert under strict CSP.

---

## 12. Clarifying questions (must be answered before implementation)

**Market & GTM**
1. **Beachhead confirmation:** do we lead with **accountancy practices** (multi-client) or **direct-to-SME**? This changes the first console we build (multi-client management vs. single-workspace polish).
2. Which **2–3 countries** first? (Determines VAT rules, e-invoice formats, retention periods, and language priorities.)
3. Is the **transport/fleet** angle (vehicle dimension, fuel, VAT refund) a primary vertical or a secondary proof point? (Affects the sibling VAT-refund product's relationship to this one.)

**Product & scope**
4. Is **outbound issuing** part of the v1 *sale* or a pure upsell? (It's built; the question is positioning and pricing.)
5. Do we commit to **employee expenses** as a near-term module, or freeze it? (It's partially built; keeping it warm has a maintenance cost.)
6. What is the **minimum viable ERP export** target — DATEV, Xero, QuickBooks — and in which order?

**Compliance & legal (block launch if unanswered)**
7. Confirm **default retention** (proposing 10y, tenant-configurable) and the **erasure-vs-retention** policy with counsel.
8. Confirm **data-residency** commitment and the **sub-processor list** (esp. any AI/OCR vendor and their region).
9. Do we need a **signed DPA + Art. 30 record** before the first paying customer? (Assume yes for accountants/regulated SMEs.)
10. What is our stance on **AI processing of customer documents** — default-off + EU-hosted only, or allow opt-in external models with SCCs?

**Commercial**
11. Primary **billing metric**: documents processed/month, seats, or a hybrid? (See [pricing](./pricing-hypothesis.md).)
12. Which **billing provider** (Stripe vs. a merchant-of-record like Paddle for EU VAT handling on our own invoices)?

**Data & migration**
13. Do customers need **bulk import** of historical invoices at onboarding, and from which sources?
14. What is the **activation event** we optimise onboarding toward (proposed: first period with ≥N invoices captured *and* first export/dashboard viewed — see [metrics](./metrics.md))?

---

## 13. Decision summary

- **Sell one thing first:** multi-channel AP capture + VAT-correct record + spend analytics, to **accountants and multi-entity SMEs**. Issuing is a built, differentiating **attach**, not the wedge.
- **Cut hard for v1:** no cards, no lending, no open banking, no public benchmark, no tax filing, no portal-scraping at scale, no self-host.
- **Compliance is a feature, not a footnote:** EU residency, a DPA, immutable audit, and a **10-year default retention with legal hold** are near-term, not "later."
- **The moat** is the proprietary multi-channel line-item dataset + trustable VAT/FX correctness. Protect it; don't dilute focus chasing adjacent products.
- **Before code:** answer the 14 clarifying questions — especially beachhead (Q1), countries (Q2), retention/residency (Q7–Q10), and billing metric (Q11).
