# Product Roadmap

**Purpose:** sequence the move from a solid internal back-office tool into a **self-feeding,
revenue-generating, pan-EU platform** — recover more VAT faster, remove manual work, de-risk
compliance, and monetise. Ties together `docs/STRATEGY.md` (the why + monetisation models) and
`docs/BACKLOG.md` (the concrete items). Organised by **business outcome**, then phased into
delivery horizons. Direction, not a contract — revisit each quarter against the KPIs.

## The outcomes everything ladders up to

| Goal | Money question | KPI |
|---|---|---|
| **Recover more VAT, faster** | Are we owed VAT, and is every claim airtight before the deadline? | € recovered / € claimable · days-to-refund · % rejected · deadline misses (target 0) |
| **Remove manual work** | How many hours per close; how much re-keying / invoice chasing? | hours/close · % invoices auto-captured · exceptions only |
| **Cut fuel spend** | Are we paying a competitive net price; where are we overcharged? | € overcharges identified → recovered |
| **De-risk compliance** | If audited tomorrow, can we prove every number? | audit-ready in minutes; evidence reproducible |
| **Monetise** | Where's the recurring revenue and the financing margin? | ARR · contingency € · financing origination € |

## What's already shipped (no longer roadmap)
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

## Phase 0 — Foundation for the self-feeding platform (now, in-repo, no partner)
*Prerequisites for everything else; highest ROI is removing manual babysitting.*
- **D6 — dedicated worker tier** (`FFS_ROLE=web` + `python waiting_room.py --work`). The execution
  substrate for automated fetching/scraping; isolates heavy out-of-band work from the web app.
- **Per-supplier rate-limit / concurrency / backoff / circuit-breaker** primitive on the intake
  queue — so fetching never overloads our system *or* trips a supplier's anti-bot/ban.
- **Test coverage** for `invoice_control`/`ingest`/`build_master`/`history`.
- **One-click monthly close** behind a guarded button with a live progress log.
- **Per-event notification alerts + SMTP relay UI** (digest already done).

## Phase 1 — Automated document capture (the flagship; builds the moat)
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

## Phase 2 — Revenue: direct-to-fleet recovery + expense/export
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

## Phase 3 — Profit centre: embedded finance + open banking (partner-gated)
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

## Phase 4 — Intelligence, scale & the SaaS surface
*Compound the data advantage; make it a sellable platform.*
- **Multi-tenant SaaS hardening** — tenant context enforced at every query (RLS / tenant-scoped
  keys), automated cross-tenant access tests, per-tenant encryption. *A cross-tenant leak is a GDPR
  Art. 33/34 breach — isolation must be provable.* + SOC 2 / ISO certification.
- **Validated Postgres cutover** (`docs/SCALING.md`) — for multi-client volume.
- **External pooled benchmark (SALE)** — anonymised cross-fleet fuel-price/markup intelligence, via
  an **independent-trustee / aggregation** model (2023 Horizontal Guidelines; hub-and-spoke risk) +
  counsel. Internal benchmark already shipped.
- **SaaS-to-agencies (white-label)** the recovery engine; **API v2** (rate-limits/quotas, key-expiry,
  write/extract scopes).
- **AI copilot** over the audited data (grounded, no hallucinated figures); e-filing integrations
  with tax-authority portals; driver/ops mobile surface (nearest cheap station).

---

## Sequencing principles
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

## Near-term concrete steps (from `docs/BACKLOG.md`)
- Phase 0: D6 worker tier · per-supplier rate-limiter · test coverage · one-click close.
- Phase 1: API ingestion + portal-scraping (both) on the worker tier · credential-custody hardening.
- Phase 2: SAF-T/e-invoice/ERP export · expense reports · direct-to-fleet recovery packaging.
