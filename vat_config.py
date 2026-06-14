"""
VAT REFUND CONFIG (EU Directive 2008/9/EC - electronic cross-border refund)
Permanent reference data for the refund module. Extend when suppliers/entities change.
Yellow INPUT markers = data to confirm from the physical invoice before submission.
"""

# Home member state portal per entity (application is filed in the HOME country portal,
# which forwards it to the refund country)
HOME_PORTAL = {
    "Jupiter Plus AS": ("EE", "Estonian e-MTA (emta.ee)"),
    "SIA OMUSS": ("LV", "Latvian EDS (eds.vid.gov.lv)"),
    "UAB Motiejausko Transportas": ("LT", "Lithuanian Mano VMI / EPRIS"),
    "UAB Vestroidas": ("LT", "Lithuanian Mano VMI / EPRIS"),
    "UAB Zaukos Transportas": ("LT", "Lithuanian Mano VMI / EPRIS"),
}

# Issuer (the party whose VAT number appears on the invoice) per supplier; None = INPUT
ISSUERS = {
    "Q8":    ("Kuwait Petroleum / Q8 (per-country entity)", None,
              "Per-country invoices; BE issuer VAT INPUT from invoice BEOI00118939"),
    "BP":    ("B2Mobility GmbH / BP Europa (PL invoice)", None, "NIP on invoice 0261167596 - INPUT"),
    "TFC":   ("TFC by Moya", None, "BE VAT number on invoice 26056012270 - INPUT"),
    "E100":  ("E100 International Trade sp. z o.o.", "BE0676647155", "printed on invoices"),
    "MOEVE": ("Moeve Pro Services, S.A.U.", "ESA25009192", "CIF printed on invoice"),
    "DKV":   ("DKV Euro Service GmbH + Co. KG", "SE502044770101", "Swedish VAT ID printed on invoices"),
}

# Known invoices per (supplier, refund country): list of (invoice_no, invoice_date)
INVOICES = {
    ("Q8", "Belgium"):  [("BEOI00118939", "2026-05-31")],
    ("Q8", "Germany"):  [("DEVR00473179", "2026-05-31")],
    ("Q8", "France"):   [("INPUT: FR country invoice", "2026-05-31")],
    ("Q8", "Spain"):    [("INPUT: ES country invoice", "2026-05-31")],
    ("Q8", "Denmark"):  [("INPUT: DK country invoice", "2026-05-31")],
    ("Q8", "Poland"):   [("INPUT: PL country invoice", "2026-05-31")],
    ("Q8", "Austria"):  [("INPUT: AT country invoice", "2026-05-31")],
    ("BP", "Poland"):   [("0261167596", "2026-06-01")],
    ("TFC", "Belgium"): [("26056012270", "2026-05-31")],
    ("E100", "Belgium"): [("BE98759/5539713", "2026-05-15"), ("BE99954/5586443", "2026-05-31")],
    ("MOEVE", "Spain"): [("BA72400000187538", "2026-05-31")],
    ("DKV", "Sweden"):  [("26/651689595/011", "2026-05-15"), ("26/652169828/011", "2026-05-31")],
}

# Goods codes per Reg. (EC) 1174/2009 / Reg. (EU) 79/2012 Annex III (2008/9/EC Art. 9)
GOODS_CODE = {
    "Diesel": ("1", "Fuel"),
    "HVO": ("1", "Fuel"),
    "Promo adj": ("1", "Fuel (price correction)"),
    "AdBlue": ("10", "Other - operating fluid (AdBlue)"),  # code 3 ("means of transport") is a defensible alternative — confirm per refund country
    "Toll/Fees": ("4", "Road tolls and road user charges"),
    "Parking": ("10", "Other - parking"),
    "Service/Other": ("10", "Other"),
}

# Minimum claim amounts (EUR base — Directive 2008/9/EC Art. 17: a sub-year period
# must reach €400, a full-year period €50, "or the equivalent in national currency").
MIN_QUARTER, MIN_ANNUAL = 400.00, 50.00

# Per-refund-country national-currency minimums, where the law fixes a specific local
# amount (NOT a live FX conversion of the EUR base). Keyed by the EXACT refund-country
# string the system stores on a claim (vat_applications.refund_country / the `country`
# dimension on transactions) — full English names, e.g. "Sweden".
# Value = (currency, sub-year/quarterly minimum, full-year/annual minimum).
# Cite: Directive 2008/9/EC Art. 17. VERIFY against current national law before relying
# on these; an admin can override the gate per claim (see vat_refund.below_minimum).
# Euro countries and Poland are INTENTIONALLY absent: they fall back to the EUR base
# (€400/€50) compared on the claim's vat_eur.
NATIONAL_MINIMUMS = {
    "Sweden":  ("SEK", 4000, 500),   # SEK 4 000 / 500 fixed in Swedish law
    "Denmark": ("DKK", 3000, 400),   # DKK 3 000 / 400
}

def min_for(country, is_annual):
    """The applicable minimum-claim threshold for a refund country and period kind.

    Returns (currency, threshold, basis) where:
      * basis == "local" — the refund country fixes a national-currency minimum
        (NATIONAL_MINIMUMS); compare against the claim's national-currency VAT
        (vat_local). `currency` is the national currency, `threshold` the local amount.
      * basis == "eur" — no distinct national amount applies (euro countries, Poland,
        and any country not in the table); compare against the claim's EUR VAT
        (vat_eur) using the EUR base. `currency` is "EUR".
    `is_annual` selects the full-year (annual) minimum, else the sub-year (quarterly).
    """
    nm = NATIONAL_MINIMUMS.get(country)
    if nm:
        ccy, quarter_min, annual_min = nm
        return ccy, (annual_min if is_annual else quarter_min), "local"
    return "EUR", (MIN_ANNUAL if is_annual else MIN_QUARTER), "eur"
# Final submission deadline: 30 September of the year following the refund year
DEADLINE_FMT = "{year_plus1}-09-30"

# Streams where the DB holds EUR but the claim must be filed in local currency
LOCAL_CCY_INPUT = {("Jupiter Plus AS", "Denmark"): "DKK", ("Jupiter Plus AS", "Poland"): "PLN"}

COMPLIANCE_NOTES = [
 "Fuel-card caveat (ECJ Vega International C-235/18 / Auto Lease C-185/01): confirm with each card "
 "issuer whether their scheme qualifies as a supply of fuel to you (refund via 2008/9/EC) or a "
 "financial service - some issuers (e.g. DKV, E100) instead run their own net-invoicing/refund "
 "service. Verify per contract before filing.",
 "Claims are filed in the refund country's currency; minimums (400/50 EUR) apply in national "
 "equivalents (e.g. SEK 4 000/500, PLN/ DKK equivalents).",
 "A quarterly application may be filed after the quarter ends; remaining-of-year periods are allowed; "
 "the absolute deadline for refund year Y is 30 September Y+1.",
 "Original e-invoices may be requested by the refund state; keep supplier PDFs archived per period.",
]
