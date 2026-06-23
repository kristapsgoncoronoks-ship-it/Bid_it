# Cash-recovery roadmap — deep-research memo (2026-06)

> Five-angle web-research synthesis (market · ViDA/e-invoicing · competitors · receivable
> financing · security) used to prioritise the development roadmap for the cash-recovery pivot.
> Vendor-marketing claims are flagged and separated from EC / EUR-Lex / standards-body sources.
> North-star metrics: **€ recovered · € overcharges found · days-to-refund · manual hours saved
> · deadline misses = 0.**

## Bottom line
The legal engine (Dir. 2008/9/EC claims) is **stable and not disrupted by ViDA**, so the moat
shifts from *extraction* to **coverage, entitlement-correctness, and cash-timing**. Highest-leverage
builds, in order:
1. **Automated invoice inbound** — email-inbox (probe embedded Factur-X/ZUGFeRD XML first) → **Peppol Access Point**.
2. **Diesel excise-duty refund** — a whole second revenue stream on data we already hold.
3. **Overcharge evidence-packet + claim-back** — turn `contract_audit` € into supplier action.
4. **Per-country entitlement correctness** — encode deductibility % + fuel-card-vs-end-user rule.
Then: embedded-finance go-live (`finance.py`), the "upload last quarter → see refund opportunity"
landing, security-before-SaaS, and ERP connectors.

## What the evidence says
- **The refund engine stays valid; the data pipe is being forced structured.** ViDA adopted 11 Mar
  2025, in force 14 Apr 2025, but does **not** repeal the 2008/9/EC refund procedure. National
  e-invoicing mandates land on a **2026 clock**: Belgium 1 Jan 2026; Poland KSeF Feb/Apr 2026; France
  Sep 2026; Germany receiving since 2025 / issuing 2027-28; Latvia B2G 2026 → B2B 2028. Cross-border
  DRR from 1 Jul 2030. `extract.parse_einvoice` (EN-16931 / Factur-X / ZUGFeRD) is the right primitive
  — the gap is **inbound channels**. (EC; Novutech; Sovos; vatcalc)
- **Cash-timing is a proven wedge.** The EC's 2019 study records 4-8 month waits causing cash lock-up;
  Eurowag/DKV/UTA monetise pre-financing today. Adsum advances UK VAT refunds at **~2% flat**,
  redirecting the refund; a *government* receivable factors at **90-97% advance, 1-2% fee** (near-zero
  default). Validates `finance.py`. (EC study; Adsum; SMB Compass)
- **The defensible problem is entitlement, not capture.** Fuel-VAT deductibility varies (DE ~100%, **BE
  50%**, some 0%); the **fuel-card-vs-end-user** rule (ECJ + German BMF circular Jan 2025) decides who
  can reclaim — so **~21% of businesses recover nothing**. (Lexology/BMF; OECD via VAT IT)
- **Fee model is success-fee; its weakness is opacity.** Contingency ("no win, no fee", ~15-30% of
  recovered VAT) is universal, with a pre-financing upsell; **every** incumbent hides the exact %. Our
  differentiators: **multi-card neutrality** (incumbents only recover well on their own card) and
  **transparent, deadline-governed economics**. (VAT IT; UTA/Edenred)
- **Second product on the table — diesel excise-duty refund.** Every fleet incumbent (DKV, Eurowag,
  UTA, FastVAT, Vialtis) sells it in ~7 states (BE/FR/IT/SI/HU/ES/HR) at ~**€25-33 per 1,000 L**. We
  already hold per-litre, per-station line-item data — excise is a *parallel claim engine on the same
  data*. (Vialtis; Tax Foundation)

## Proposals — prioritized

### NOW
| # | Build | Why | Codebase fit | North-star |
|---|---|---|---|---|
| 1 | Automated invoice inbound — email-inbox (embedded-XML probe first) → Peppol Access Point | Less manual upload = more value; 2026 mandates; Peppol = broadest EU channel | `extract.parse_einvoice` + `waiting_room` exist; add email→queue ingestor + AP/SMP connector | manual hours saved; coverage |
| 2 | Diesel excise-duty refund module | Every incumbent sells it (7 countries, €25-33/1,000 L); we hold the litre data | New claim engine over `fuel_history` (per-country excise rates in config) | **€ recovered (new stream)** |
| 3 | Overcharge evidence-packet + claim-back workflow | No incumbent does line-item overcharge claim-back | Reuse `evidence_pack` + `contract_audit`; add a claim-back state machine | **€ overcharges recovered** |

### NEXT
| # | Build | Why | Fit | Metric |
|---|---|---|---|---|
| 4 | Per-country entitlement correctness (deductibility %, card-vs-end-user) | Where 21% recover nothing; CJEU C-234/24 (Oct 2025) substance-over-form | `vat_config.py` + checklist gates | € recovered (accuracy); deadline misses = 0 |
| 5 | Embedded-finance go-live (`finance.py` → real LaaS partner advance, recourse, gate-passed only) | Proven economics; counsel-gated; CRD VI defines factoring as lending | `finance.py` seam shaped | days-to-refund → ~0 |
| 6 | "Upload last quarter → see refund opportunity" self-serve landing | The best-first-offer; instant € VAT + € overcharge estimate | Reuse `recovery_dashboard` + deterministic capture | pipeline / conversion |

### LATER / platform (before multi-client SaaS)
| # | Build | Why |
|---|---|---|
| 7 | Security hardening — wire `tenancy.scope_clause()` per-table (defense-in-depth), HSM/KMS + per-tenant BYOK in `keyvault`, ship Art. 28 DPA + Art. 30 RoPA + 72h breach runbook, start SOC 2 Type II clock + design for ISO 27001/27017/27018 | A cross-tenant tax-data leak is an Art. 33/34 reportable breach; SOC 2/ISO gate enterprise procurement; 6-12 mo lead time |
| 8 | ERP connectors — Xero / QuickBooks / DATEV on the existing ledger-CSV/SAF-T/e-invoice hub | Finance-team stickiness; lower urgency than capture/recovery |

## Confidence & caveats
- **No neutral € figure exists for unclaimed *fuel* VAT.** The viral "€20B / €2B unclaimed" figures are
  unmethodologied vendor claims that **mutually conflict** — do NOT use in a pitch. Solid: ~700k
  cross-border refund applications/yr (EC 2019); **~21% recover nothing** (OECD, widely cited).
- **Finance = counsel-gated.** Factoring is licence-exempt in some EU states but **licensable in others
  (e.g. Germany)**; **CRD VI** pulls factoring under EU licensing; factoring fees are VAT-taxable (CJEU
  C-232/24). Keep `finance.py` advisory/NullProvider until a regulated partner + per-country structure
  clears.
- **Country e-invoicing dates have slipped before** (Poland, France) — verify near go-live; sequence
  connectors by where the fleet actually fuels.
- **Excise rates change yearly and depend on eligibility** (commercial diesel, vehicle ≥7.5 t,
  card-paid) — any excise figure is an **advisory estimate**; rates must be admin-set/verified.

## Sources (primary first)
- EC — VAT refunds & ViDA: https://taxation-customs.ec.europa.eu/taxation/vat/vat-directive/vat-refunds_en · https://taxation-customs.ec.europa.eu/taxation/vat/vat-digital-age-vida_en
- EC 2019 VAT Refunds study: https://taxation-customs.ec.europa.eu/system/files/2019-05/vat-refunds-final-report.pdf
- Dir. 2008/9/EC (EUR-Lex): https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=CELEX:32008L0009 · Dir. 2006/112/EC: https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=celex%3A32006L0112
- ViDA adoption (Council): https://www.consilium.europa.eu/en/press/press-releases/2025/03/11/
- e-invoicing mandates: https://www.novutech.com/news/e-invoicing-in-europe-overview-of-mandates-2025-2027 · https://sovos.com/vat/tax-rules/latvia-e-invoicing/ · https://ec.europa.eu/digital-building-blocks/sites/spaces/DIGITAL/pages/467108886/eInvoicing+in+Germany
- Standards/Peppol: https://saft-validator.com/blog/peppol-en16931-einvoicing-explained · https://invostaq.com/blog/en-16931-european-einvoicing-standard-explained
- Competitors: https://www.eurowag.com/services/tax-refund · https://www.dkv-mobility.com/en/financial-services/tax-refund/vat/ · https://finance.edenred.com/how-to-refund-vat-and-excise-duties/ · https://fastvat.com/vat-refund-2/ · https://www.vialtis.com/en/our-services/vat-refund · https://vatit.com/industry/automotive-mobility/
- Excise (diesel) data: https://taxfoundation.org/data/all/eu/diesel-gas-taxes-europe/
- Finance: https://mtd.adsum-works.com/instant-vat-refunds · https://www.smbcompass.com/government-contract-invoice-factoring/ · https://www.basikon.com/en/articles/becoming-lender-as-a-service-guide-vertical-saas-embedded-finance-2026 · CRD VI: https://www.mayerbrown.com/en/insights/publications/2025/04/refresher-eu-capital-requirements-directive-6-crd6 · CJEU C-232/24: https://www.pwc.nl/en/insights-and-publications/tax-news/vat/cjeufactoringvattaxableasdebtcollectionservices.html
- Fuel-card entitlement (ECJ + German BMF Jan 2025): https://www.lexology.com/library/detail.aspx?g=d977d21f-2059-4df3-8d91-1c0cef29a42c
- Security/compliance: GDPR Art. 28/30/33/34 https://gdpr-info.eu/art-28-gdpr/ · SOC 2 https://www.thoropass.com/blog/soc-2-audit-cost-a-guide · ISO https://www.barradvisory.com/resource/iso-27001-27002-27701-27017-27018/ · tenant isolation https://workos.com/blog/cryptographic-key-isolation-multi-tenant-saas · KMS envelope https://docs.cloud.google.com/kms/docs/envelope-encryption
