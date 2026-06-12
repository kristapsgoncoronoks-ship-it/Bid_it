# EU cross-border VAT refund — rules reference (Directive 2008/9/EC)

The compliance reference the VAT-refund module must honour. It states the verified legal
parameters, the standardised expenditure codes, country-level diesel recoverability, and a
**cross-check against what `vat_config.py` / `vat_refund.py` actually encode** — including
issues to fix.

> **Sourcing caveat.** Compiled June 2026 via multi-source web research. Direct page fetches to
> EUR-Lex and tax-authority sites were blocked (HTTP 403) in the research environment, so
> verbatim article text came from search-engine extracts of those same official pages,
> cross-corroborated across multiple independent sources. Figures are internally consistent and
> multiply-confirmed; **before citing in a filing, confirm exact wording against the EUR-Lex PDF**
> (`https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32008L0009`). Per-country and
> sub-code specifics from commercial VAT-reclaim vendors are flagged MEDIUM confidence.

---

## 1. The core parameters (what the engine gates on)

| Rule | Value | Article | In our code |
|------|-------|---------|-------------|
| **Eligibility** | Taxable person **not established** in the refund state, who made **no supplies** there in the period — except exempt transport/ancillary services and reverse-charge supplies | Art. 3 (+ Art. 4 exclusions) | implicit (hauliers qualify) |
| **Procedure** | **One electronic application** filed via the **home-state portal**, which validates & forwards to the refund state; no paper invoices up front | Art. 7 | `vat_config.HOME_PORTAL` ✓ |
| **Refund period** | Min **3 calendar months** (a quarter), max **1 calendar year**; a shorter period only if it's the **remainder of the year** | Art. 16 | quarterly + annual + dynamic merge ✓ |
| **Minimum amounts** | **€400** for a 3-month-to-<year period; **€50** for a full year or remainder | Art. 17 | `MIN_QUARTER, MIN_ANNUAL = 400, 50` ✓ |
| **Deadline** | **30 September of the year following** the refund period — a **strict, fatal time-bar** (CJEU C-294/11 *Elsacom*: miss it → right forfeited) | Art. 15 | `filing_deadline()` + hard `period_ended()` gate ✓ |
| **Local currency** | Non-euro refund states set local-currency equivalents of €400/€50; claims filed in the refund state's currency | Art. 17 | `LOCAL_CCY_INPUT`, compliance notes ✓ (EE/LV/LT are euro, so literal figures apply) |

## 2. Required application content (Art. 8 — all mandatory; a claim is "submitted" only when complete, Art. 15)

- **Application-level (Art. 8(1)):** name & address; electronic contact; **business-activity description via harmonised NACE codes** (Art. 11); refund period; the **"no supplies" declaration** (with the Art. 3(b) carve-outs); VAT/tax-reference number; **bank account IBAN + BIC**.
- **Per-invoice (Art. 8(2)):** supplier name/address; supplier VAT number + refund-state prefix; invoice date & number; **taxable amount and VAT amount in the refund state's currency**; **amount of deductible VAT**; **deductible proportion as a % where applicable** (Art. 8(2)(g)); **nature of goods/services by the Art. 9 expenditure code**.

## 3. The expenditure codes (Art. 9; sub-codes per Reg. (EC) 1174/2009 → Reg. (EU) 79/2012 Annex III)

**Top-level codes 1–10** (each application line carries one):

| Code | Meaning |
|------|---------|
| **1** | **Fuel** |
| 2 | Hiring of means of transport |
| 3 | Expenditure relating to means of transport (other than codes 1 & 2) |
| **4** | **Road tolls and road user charge** |
| 5 | Travel expenses (taxi, public transport) |
| 6 | Accommodation |
| 7 | Food, drink and restaurant services |
| 8 | Admissions to fairs and exhibitions |
| **9** | **Expenditure on luxuries, amusements and entertainment** |
| **10** | **Other** |

**Fuel sub-codes (code 1)** — the part that signals *vehicle type* to the refund authority. A
refund state that opts into Art. 9(2) requires these:

- **1.1 — Fuel for means of transport with a mass GREATER than 3 500 kg** (other than for paying passengers) → **1.1.1 petrol · 1.1.2 diesel · 1.1.3 LPG · 1.1.4 natural gas · 1.1.5 biofuel**
  → **A haulier's truck diesel is code `1.1.2`.**
- 1.2 — Fuel for means of transport **≤ 3 500 kg** (other than paying passengers) → .1–.5 by fuel type
- 1.3 — Fuel for means of transport **for paying passengers** → .1–.5
- (1.4 test vehicles · 1.5 lubricants · 1.6 resale · 1.7 goods-transport fuel · 1.8 cars/multipurpose · 1.9 recreational — Member-State-dependent; MEDIUM confidence on exact numbering)

**Codes 2 (hiring) and 3 (other vehicle expenditure) carry the same two-axis split** — mass
> 3 500 kg vs ≤ 3 500 kg, and "for paying passengers" — so vehicle hire/maintenance for a truck is
also classified on the goods-vehicle axis. (Origin Reg. (EC) 1174/2009, superseded by Reg. (EU)
79/2012 with effect 20 Feb 2012; codes carried over essentially unchanged.)

The EU publishes which Member States require the Art. 9(2) sub-codes vs accept the bare top-level
code: https://taxation-customs.ec.europa.eu/system/files/2016-09/information-document_en.pdf

## 4. Invoice copies & documentation (Art. 10)

The refund state **may require a scanned copy** of the invoice/import document where the **taxable
amount is ≥ €1 000** (or local equivalent) — **and ≥ €250 for FUEL** (the lower fuel threshold,
confirmed). We archive every PDF in the vault regardless, so this is satisfied de facto, but the
**€250 fuel / €1 000 general** thresholds are the trigger. Separately, under **Art. 20(1)** the
refund state may demand the invoice (original or copy) **regardless of these thresholds** whenever it
has reasonable doubt — another reason every original stays in the vault.

## 5. Processing timeline, information requests & interest (Arts. 19–27)

- **Receipt:** refund state notifies the receipt date without delay (Art. 19(1)).
- **Decision ladder:** **4 months** base (Art. 19(2)) → **6 months** if it requests additional info (Art. 20/21) → **8 months** maximum if it requests *further* info (Art. 21).
- **Applicant's response window:** **1 month** to supply requested info (Art. 20(2)) — **NOT a preclusive deadline** (CJEU C-133/18 *Sea Chefs*): missing it does **not** auto-forfeit the claim; the info can still be supplied at appeal. **Model 2B as a prompt, never a hard gate.**
- **Payment:** within **10 working days** of the decision-deadline expiry (Art. 22).
- **Refusal:** must be reasoned; appealable under the refund state's **national** rules/time-limits (Art. 23).
- **Interest on late refunds:** owed to the applicant when paid late (Arts. 26–27), at the refund state's national rate — *recoverable money*. Exceptions: applicant failed to supply requested info, or Art. 10 documents not yet received.
- There is **no literal "deemed approval"** clause; it's a derived/national remedy.

## 6. Deductibility is the REFUND STATE's national law — never propagate across countries

- **Art. 5(2):** "entitlement to an input tax refund shall be determined pursuant to Directive 2006/112/EC **as applied in the Member State of refund**." → *What's refundable = what's deductible under the refund country's own VAT law.*
- **Art. 6 / Art. 13:** if the applicant makes exempt supplies at home, the refund is scaled by its **home-state pro-rata**, with year-end adjustment. **Two independent clips:** (a) refund-state category deductibility, (b) home-state pro-rata.
- **Art. 176 standstill (2006/112):** Member States keep pre-existing exclusions (luxuries/entertainment, often passenger-car fuel, restaurants) — *why* the same expense differs by country and why exclusions are sticky.
- **Consequence for the system:** model refundability as a **per-(refund_country, expense_category)** table that **defaults to "needs-confirmation", not "refundable."** Diesel for trucks is the safe case (below); non-fuel items are not.

## 7. Commercial-truck diesel VAT recoverability by country (8th-Directive, 2026)

Truck (goods-vehicle) diesel VAT is **fully recoverable (100%)** in all 12 markets a Baltic haulier
fuels in — the 50%/exclusion caps attach to **passenger cars**, not goods vehicles.

| Country | Truck-diesel VAT | Std VAT 2026 | Note | "Professional diesel" EXCISE refund (separate from VAT) |
|---------|------------------|--------------|------|--------------------------------------------------------|
| Poland | 100% | 23% | car caps don't touch trucks | No |
| Germany | 100% | 19% | authority procedurally strict | No |
| Belgium | 100% | 21% | trucks/vans full; 50% car cap N/A | **Yes** (≥7.5t; customs registration) |
| Netherlands | 100% | 21% | — | No |
| France | 100% | 20% | petrol aligned to diesel by 2022 | **Yes** (ex-TICPE; new 2025 rules for non-FR) |
| Lithuania | 100% | 21% | usually home state (domestic deduction) | No |
| Latvia | 100% | 21% | usually home state | No |
| Estonia | 100% | **24%** (since Jul 2025) | 50% cap on cars only | No |
| Czechia | 100% | 21% | — | No |
| Austria | 100% | 20% | cars non-deductible; lorries full — watch classification | No |
| Italy | ~100% (diesel, AdBlue, tolls) | 22% | — | **Yes** (quarterly *rimborso accise*) |
| Spain | 100% | 21% | — | **Yes** (*gasóleo profesional*; non-resident NIF + approved fuel card since Oct 2022) |

**The excise "professional diesel" rebate is a different regime** — a partial **excise-duty** refund
(not VAT), claimed from each country's **customs** authority, only for trucks **≥ 7.5 t**, not
harmonised, rates change quarterly. Available in **FR, BE, IT, ES, SI, HU** (of the above). Keep it
an **entirely separate workflow** from the VAT engine.

---

## 8. Cross-check: our code vs the rules

### ✓ Correct
- `MIN_QUARTER/MIN_ANNUAL = 400/50`, `filing_deadline()` = 30 Sep Y+1 with a hard period-end gate, `HOME_PORTAL` forward model, the `LOCAL_CCY_INPUT` note, and the **fuel-card caveat** (Vega International C-235/18) are all accurate.
- **2B (document request) is modelled as a soft worklist reminder, not an auto-reject** — which is exactly right per *Sea Chefs* (the Art. 20 one-month window is non-preclusive). **Do not ever "harden" it into a forfeiture gate.**

### ⚠ Fix — expenditure mis-codings (`vat_config.GOODS_CODE`, risk of rejection)
1. **Road tolls coded `3`, should be `4`.** `"Toll/Fees": ("3", "Road tolls and road user charges")` — but Art. 9 code **4** *is* "road tolls and road user charge"; code 3 is the generic "expenditure relating to means of transport." The description already says "road tolls," so the **code number is simply wrong**. → change to `("4", …)`.
2. **AdBlue / Parking / Service coded `9`, should be `10`.** These map to code **9**, whose official meaning is **"Expenditure on luxuries, amusements and entertainment"** — the archetypal *non-deductible* category. The intended "Other" bucket is code **10**. Submitting an operating fluid (AdBlue) or parking under the luxuries/entertainment code invites refusal. → remap AdBlue/Parking/Service/Other to `("10", "Other")` (AdBlue arguably fits code 3 "expenditure relating to means of transport" — confirm per refund country).
3. **Diesel emits only the top-level code `1`.** For refund states that require Art. 9(2) sub-codes, truck diesel should be **`1.1.2`** (mass > 3 500 kg, diesel). Consider extending `GOODS_CODE` to carry the sub-code where the refund country requires it (the EC list says which do).

### ◻ Gaps / opportunities (not bugs for these entities, but note)
- **No home-state pro-rata / deductible-proportion (Art. 8(2)(g), Arts. 6/13).** Fine *only if* every entity makes purely taxable transport supplies (100% deduction) — the normal haulier case. Record the assumption; if any entity has exempt income, the refund must be scaled and the % reported.
- **Non-fuel items aren't checked for per-country deductibility (Art. 5).** Diesel is safe everywhere; tolls/AdBlue/parking deductibility varies by refund country — don't assume refundable.
- **Interest on late refunds (Arts. 26–27) isn't tracked.** The Recovery page flags ">120 days unpaid" heuristically; the statutory model is the 4/6/8-month decision ladder + pay-within-10-working-days, after which **interest is owed**. Tracking it surfaces recoverable money.

### Key legal references for the engine
2008/9/EC Arts. 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 16, 17, 19, 20, 21, 22, 23, 26, 27 ·
Reg. (EC) 1174/2009 / Reg. (EU) 79/2012 Annex III (codes) · 2006/112/EC Arts. 168–176 (deduction,
standstill) · CJEU C-294/11 *Elsacom* (deadline is fatal) · CJEU C-133/18 *Sea Chefs* (Art. 20
window not preclusive) · CJEU C-235/18 *Vega International* (fuel-card supply vs financial service).
