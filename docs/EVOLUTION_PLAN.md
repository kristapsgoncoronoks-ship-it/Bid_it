# Evolution Plan — how the app must evolve to be worth it

A concrete, costed, sequenced plan that deep-dives **each idea** in `docs/ROADMAP.md` /
`docs/STRATEGY.md` / `docs/BACKLOG.md`. Each item: **what + why it's worth it · how to build it
(the in-repo seam) · effort/cost · dependencies · #1 risk · success metric.** Grounded in the
codebase + three deep-research passes (figures are indicative ranges with confidence; provider
prices are quote-based — model, don't quote). Currency mixes EUR/USD as sourced.

## The "make-it-worth-it" milestone (north star)
The platform crosses from **cost-centre → sellable product** when it is **self-feeding and
multi-tenant with at least one paying external customer on a repeatable contract.** Concretely:
(1) invoices flow in **automatically** for most suppliers (API/e-invoicing + scraping) with no
manual upload; (2) a **second, unrelated company** is onboarded behind **provable tenant
isolation**; (3) at least one **revenue line is live** (recovery contingency, a per-vehicle
subscription, or financing origination). Everything below ladders to that.

---

## Phase 0 — Foundation (Q1; in-repo, no partner) — *unblocks everything*

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

## Phase 1 — Automated document capture (Q1–Q3; the moat engine)
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

## Phase 2 — Revenue (Q3–Q4; turn data into cash + a finance-dept product)

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

## Phase 3 — Profit centre (Q4–Q6; embedded finance + open banking — partner-gated)

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

## Phase 4 — SaaS & scale (Q5–Q8; make it a sellable platform)

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

**4.3 Validated Postgres cutover** (`docs/SCALING.md`) — exercise `_PgShim` on real psycopg, port
dialect-isms (`datetime('now')`→`now()`, `INSERT OR IGNORE`→`ON CONFLICT`, audit triggers →
PG trigger fn), move queue + leases to the shared DB, `pg_dump` backups. *Why:* the substrate for
multi-tenant volume. *Effort:* L (needs a live Postgres). *Metric:* full suite green on PG.

**4.4 External pooled benchmark (SALE).** *Why:* a premium data product on the accumulated dataset.
*Build/Gate:* anonymised cross-fleet fuel-price/markup intelligence via an **independent-trustee /
aggregation** model (2023 Horizontal Guidelines; **hub-and-spoke** risk; no firm-level/forward data)
+ counsel + the Data-Act unfair-terms regime. Internal benchmark already shipped. *Effort:* M + legal.
*Metric:* benchmark subscribers; counsel sign-off.

---

## Cross-cutting

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

## The recommended 18-month sequence (quarter by quarter)
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

## The 3 decisions the owner must make FIRST
1. **Go multi-client SaaS now, or stay per-deployment for the Baltic entities first?** — gates the
   multi-tenant RLS work (4.1) and how early Phase 4 starts. (Recommend: build capture + revenue
   single-tenant first; add multi-tenancy when the first external customer is real.)
2. **KMS backend: cloud KMS (AWS/GCP/Azure) vs HashiCorp Vault?** — gates credential custody (1.4),
   the prerequisite for self-service onboarding and the whole scraping programme.
3. **Embedded-finance partner: pursue Factris (or another licensed factor) for a VAT-receivable
   advance product?** — the highest-margin lever; needs early outreach (no public precedent — confirm
   a live product), and it shapes whether Phase 3 is real.

## Make-it-worth-it milestone (restated, measurable)
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
