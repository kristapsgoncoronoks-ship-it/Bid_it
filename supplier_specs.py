"""
SUPPLIER REGISTRY - the "trainable" layer of the fuel consolidation system.

Each supplier = one SPEC entry. The engine (consolidate.py) and the renderer
(build_master.py) never change; adding a supplier means adding one spec here.

HOW TO ONBOARD A NEW SUPPLIER (one-time training, ~30 min):
  1. Get the first invoice PDF + (ideally) the portal transaction export.
  2. Build the supplier workbook in the standard layout: a 'Transactions'
     sheet with one row per line item + control sheets (copy any existing
     build_*.py as a starting point, or import the CSV directly).
  3. Copy _TEMPLATE below, rename, and fill in:
       - metadata: entity, scope, currency, quirks, controls, portal, terms
       - row_map(): ~5-10 lines mapping the workbook columns to the
         canonical fields (see CANONICAL_FIELDS)
       - expected: headline figures FROM THE INVOICE (line count, gross
         total, diesel litres...). This is the training target.
  4. Add the workbook filename to FILES in month_config.py.
  5. Run consolidate.py. The engine maps every line, recomputes the
     headline figures and compares them to 'expected'. PASS = the supplier
     is trained and automatically appears in every master view (benchmark,
     entity/VAT, scorecard, specs sheet). FAIL = fix the row_map and rerun.

CANONICAL_FIELDS (what row_map must return):
  vehicle, date, time, station, product, qty,
  net_local, vat_local, gross_local            (document currency)
  net_eur, vat_eur                             (EUR; engine helpers available)
  net_eur_eff (optional - e.g. rebate-adjusted), note (optional),
  country (optional - else spec default)
"""

# Central product dictionary - extend when a new supplier uses new names
PRODUCT_GROUPS = {
    "Diesel":  ("DIESEL", "ON ACT", "GASOLEO", "GASOIL", "GAZOLE", "ON "),
    "HVO":     ("HVO",),
    "AdBlue":  ("ADBLUE", "ADB.", "ECOBLUE", "AD BLUE"),
    "Parking": ("PARK",),
    "Toll/Fees": ("TOLL", "ORS", "MAUT", "PEAGE"),
    "Promo adj": ("PROMO",),
}

def prod_group(p):
    p = (p or "").upper()
    for grp, keys in PRODUCT_GROUPS.items():
        if grp == "Diesel":  # HVO/Promo checked first below
            continue
        if any(k in p for k in keys):
            return grp
    if any(k in p for k in PRODUCT_GROUPS["Diesel"]):
        return "Diesel"
    return "Service/Other"
# resolve precedence: HVO & Promo before Diesel
_pg = prod_group
def prod_group(p):  # noqa: F811
    u = (p or "").upper()
    if "PROMO" in u: return "Promo adj"
    if "HVO" in u: return "HVO"
    return _pg(p)


SPECS = {
# =====================================================================
"Q8": dict(
    entity=("Jupiter Plus AS", "EE", "EE100127540"),
    scope="BE FR DE ES DK PL AT", currency="multi", country="per-line",
    quirks=("Q8 payment summary lists transactions at LIST price per country/currency; "
            "Port One issues SEPARATE rebate invoice per country - always reconcile the PAIR. "
            "Rebate/L allocation is an assumption (blue input cells in adjusted workbook)."),
    controls="net = price x qty +-0.01; VAT per country rate +-0.02; per-line FX for DKK/PLN.",
    portal="Q8 e-invoice portal; Port One account manager",
    terms="Port One net payable due ~14th next month",
    sheet="Transactions",
    expected={"lines": (94, 0), "net_eur": (56057.99, 0.05)},
),
"BP": dict(
    entity=("SIA OMUSS", "LV", "LV-reg"),
    scope="Poland", currency="PLN", country="Poland",
    quirks="Diesel 8% VAT / AdBlue+tolls 23%. Split payment (MPP) mandatory. A2 tolls carry ~2.5% ORS fee lines.",
    controls="Net+VAT=Gross +-0.01; implied unit vs doc +-0.01; VAT = net x rate +-0.02.",
    portal="BP/Aral fleet portal CSV", terms="Due 15th next month, split payment",
    sheet="Transactions",
    expected={"lines": (46, 0), "gross_local": (72215.36, 0.02), "net_eur": (15621.23, 0.05)},
),
"TFC": dict(
    entity=("UAB Motiejausko Transportas", "LT", "LT-reg"),
    scope="Belgium", currency="EUR", country="Belgium",
    quirks=("Discount -0.205/L ONLY at TFC hubs (Meer/Meer2 -0.19, Maasmechelen); third-party "
            "stations (Romac, Texaco, De Warande) undiscounted - no discount/net columns there."),
    controls="location price - discount = net +-0.0001; amount = eff price x volume +-0.015.",
    portal="TFC customer portal", terms="Per contract",
    sheet="Transactions",
    expected={"lines": (120, 0), "net_eur": (42454.54, 0.05)},
),
"E100": dict(
    entity=("UAB Vestroidas", "LT", "LT100006205817"),
    scope="Belgium", currency="EUR", country="Belgium",
    quirks=("Two invoices per month (1-15, 16-31). Gross price minus per-litre discount; "
            "discount tiers by station color (0.08-0.23/L diesel; AdBlue to 0.60 late-May La Louviere)."),
    controls="disc = qty x rate; amount = qty x gross - disc; VAT = 21/121; net price = (amt-VAT)/qty. +-0.011.",
    portal="E100 personal account XLS export", terms="1-15 due 15th; 16-31 due 30th",
    sheet="Transactions",
    expected={"lines": (214, 0), "gross_local": (94437.14, 0.05)},
),
"MOEVE": dict(
    entity=("UAB Zaukos Transportas", "LT", "LT714494413"),
    scope="Spain", currency="EUR", country="Spain",
    quirks=("ALL amounts VAT-INCLUSIVE (diesel/gasoleo 10%, EcoBlue 21%). PRN discounts off pump PVP. "
            "Promo correction lines (qty 0). Cash-at-pump possible (pagado al contado)."),
    controls="qty x PVP - disc = final +-0.025 (vendor computes 6 decimals, displays 3).",
    portal="www.moeve.es online card management", terms="Transfer due 30th next month",
    sheet="Transactions",
    expected={"lines": (131, 0), "gross_local": (61297.15, 0.05)},
),
"DKV": dict(
    entity=("Jupiter Plus AS", "EE", "EE100127540"),
    scope="Sweden (May)", currency="SEK", country="Sweden",
    quirks=("SEK, 25% VAT, payable in EUR (FX per transaction date). Flat 1.30 SEK/L diesel discount. "
            "5.63% service fee on parking/services. HVO 100 possible."),
    controls="base = qty x net +-0.02; net = base - disc + fee +-0.011; gross = net + VAT +-0.011; VAT 25% +-0.07.",
    portal="DKV eReporting CSV/API", terms="Per DKV terms",
    sheet="Transactions",
    expected={"lines": (85, 0), "gross_local": (362420.17, 0.05), "gross_eur": (33537.19, 0.05)},
),
}

# ---------------------------------------------------------------------
# row_map functions: workbook row (tuple of cell values) -> canonical dict
# Keep them tiny; everything else is the engine's job.
# ---------------------------------------------------------------------
def _q8(r, ctx):
    card,date,time,prod,stn,ctry,vatp,cur,lp,fx,npeur,qty,net,_,_,_,reb,adjp,adjnet,_ = r[:20]
    vat = round(net*vatp/100, 2)
    return dict(vehicle=card, date=date, time=time, station=stn, product=prod, qty=qty,
                country=ctry, currency=cur,
                net_local=net, vat_local=vat, gross_local=net+vat,   # doc nets already EUR
                net_eur=net, vat_eur=vat, net_eur_eff=adjnet,
                note="eff = after Port One rebate")

def _bp(r, ctx):
    lp,card,date,reg,loc,prod,cat,qty,unit,gross,vatp,vat,net = r[:13]
    rate = ctx["fx"]["EUR_PER_PLN"]   # EUR per 1 PLN (multiply PLN -> EUR)
    return dict(vehicle=f"{card}/{reg}", date=date, time="", station=loc, product=prod,
                qty=qty, net_local=net, vat_local=vat, gross_local=gross,
                net_eur=net*rate, vat_eur=vat*rate)

def _tfc(r, ctx):
    card,date,time,rec,plate,prod,loc,vol,locp,disc,netp,amt = r[:12]
    vat = round(amt*0.21, 2)
    return dict(vehicle=f"{card}/{plate}", date=date, time=time, station=loc, product=prod,
                qty=vol, net_local=amt, vat_local=vat, gross_local=amt+vat,
                net_eur=amt, vat_eur=vat)

def _e100(r, ctx):
    inv,plate,date,time,stn,prod,qty,gp,dr,disc,np_,vat,gross = r[:13]
    if not isinstance(gross, (int, float)): return None
    return dict(vehicle=plate, date=date, time=time, station=stn, product=prod, qty=qty,
                net_local=gross-vat, vat_local=vat, gross_local=gross,
                net_eur=gross-vat, vat_eur=vat, note=inv)

def _moeve(r, ctx):
    plate,date,time,stn,con,qty,pvp,op,disc,fin,iva,cash = r[:12]
    if not isinstance(fin, (int, float)): return None
    net = fin/(1+iva/100)
    return dict(vehicle=plate, date=date, time=time, station=stn, product=con, qty=qty,
                net_local=net, vat_local=fin-net, gross_local=fin,
                net_eur=net, vat_eur=fin-net,
                note=("paid cash %.2f" % cash) if cash else "")

def _dkv(r, ctx):
    inv,veh,date,time,br,city,prod,qty,gpu,npu,base,disc,fee,net,vat,gross,eur = r[:17]
    if not isinstance(eur, (int, float)): return None
    net_eur = eur*net/gross
    return dict(vehicle=veh, date=date, time=time, station=f"{br} {city}", product=prod,
                qty=qty, net_local=net, vat_local=vat, gross_local=gross,
                net_eur=net_eur, vat_eur=eur-net_eur, note=inv)

ROW_MAPS = {"Q8": _q8, "BP": _bp, "TFC": _tfc, "E100": _e100, "MOEVE": _moeve, "DKV": _dkv}

# =====================================================================
# _TEMPLATE for a NEW supplier - copy, rename, fill, add to SPECS/ROW_MAPS
# =====================================================================
# "NEWSUP": dict(
#     entity=("<Legal entity>", "<CC>", "<VAT reg>"),
#     scope="<countries>", currency="<EUR|local>", country="<default country or 'per-line'>",
#     quirks="<format peculiarities learned from first invoice>",
#     controls="<arithmetic rules + tolerances>",
#     portal="<where to download CSV export>", terms="<payment terms>",
#     sheet="Transactions",
#     expected={"lines": (<N>, 0), "gross_local": (<invoice total>, 0.05)},
# ),
#     # SOURCE OPTIONS (omit for default xlsx workbook from month_config FILES):
#     source={"type": "xml", "file": "newsup_2026-06.xml",
#             "record_path": ".//cac:InvoiceLine", "ns": {...}, "fields": {...}},
#     # or: source={"type": "api", "url": "https://api.newsup.com/v1/transactions",
#     #             "params": {"period": "{PERIOD}"}, "auth_env": "NEWSUP_TOKEN",
#     #             "format": "json", "records_key": "data.transactions"},
#     # or: source={"type": "csv", "file": "newsup_export.csv", "delimiter": ";"},
#
# def _newsup(r, ctx):
#     # xlsx/csv source -> r is a tuple/dict of columns; xml/api source -> r is a dict:
#     # return dict(vehicle=r["vehicle"], date=r["date"], time="", station=r["station"],
#     #             product=r["product"], qty=float(r["qty"]),
#     #             net_local=float(r["net"]), vat_local=float(r["vat"]),
#     #             gross_local=float(r["net"])+float(r["vat"]),
#     #             net_eur=float(r["net"]), vat_eur=float(r["vat"]))
# def _newsup_xlsx_style(r, ctx):
#     a, b, ... = r[:K]                      # unpack workbook columns
#     return dict(vehicle=..., date=..., time=..., station=..., product=...,
#                 qty=..., net_local=..., vat_local=..., gross_local=...,
#                 net_eur=..., vat_eur=...)
# ROW_MAPS["NEWSUP"] = _newsup
