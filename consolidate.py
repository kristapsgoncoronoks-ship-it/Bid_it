"""
CONSOLIDATION ENGINE - generic; never edited when suppliers are added.
Reads supplier_specs.py (the trained registry) + month_config.py, maps every
workbook line to the canonical schema, VALIDATES each supplier against its
'expected' invoice figures, and pickles the rows for build_master.py.
Run:  python3 consolidate.py
"""
import pickle, sys
import money
from supplier_specs import SPECS, ROW_MAPS, prod_group
from month_config import PERIOD, FX, FILES
from ingest import fetch_records

import os
WORKDIR = os.path.dirname(os.path.abspath(__file__))
CTX = {"fx": FX, "period": PERIOD}

def norm_date(d):
    """Normalize supplier date formats to ISO YYYY-MM-DD (Q8/BP DD/MM/YY, TFC DD-MM-YY, others ISO)."""
    if hasattr(d, "strftime"): return d.strftime("%Y-%m-%d")
    s = str(d).strip()
    if len(s) == 10 and s[4] == "-": return s
    for sep in ("/", "-"):
        p = s.split(sep)
        if len(p) == 3 and len(p[2]) == 2 and len(p[0]) <= 2:
            return f"20{p[2]}-{p[1].zfill(2)}-{p[0].zfill(2)}"
    return s
FIELDS = ["entity","supplier","country","vehicle","date","time","station","product",
          "product_group","qty","currency","net_local","vat_local","gross_local",
          "net_eur","vat_eur","net_eur_eff","note"]

ROWS, failures = [], []
for sup, fname in FILES.items():
    if sup not in SPECS or sup not in ROW_MAPS:
        print(f"!! {sup}: no spec/row_map registered - onboard it in supplier_specs.py"); failures.append(sup); continue
    spec = SPECS[sup]; ent, _, _ = spec["entity"]
    n0 = len(ROWS)
    for r in fetch_records(sup, spec, CTX, files=FILES):
        m = ROW_MAPS[sup](r, CTX)
        if m is None: continue
        ROWS.append([ent, sup, m.get("country", spec["country"]), m["vehicle"], norm_date(m["date"]),
                     m.get("time",""), m["station"], m["product"], prod_group(m["product"]),
                     m["qty"], m.get("currency", spec["currency"]),
                     money.f2(m["net_local"]), money.f2(m["vat_local"]), money.f2(m["gross_local"]),
                     money.f2(m["net_eur"]), money.f2(m["vat_eur"]),
                     money.f2(m.get("net_eur_eff", m["net_eur"])), m.get("note","")])
    sub = ROWS[n0:]
    # ---- validation against expected (the training check) ----
    calc = {
        "lines": len(sub),
        "gross_local": money.fsum(x[13] for x in sub),
        "net_eur": money.fsum(x[14] for x in sub),
        "gross_eur": money.fsum(x[14]+x[15] for x in sub),
        "diesel_litres": round(sum(x[9] for x in sub if x[8]=="Diesel"),2),
    }
    ok = True
    for metric, (val, tol) in spec.get("expected", {}).items():
        d = abs(calc[metric]-val)
        status = "OK" if d <= tol else "FAIL"
        if status == "FAIL": ok = False
        print(f"  {sup:6} {metric:13} doc {val:>12,.2f} calc {calc[metric]:>12,.2f}  {status}")
    print(f"{'PASS' if ok else '** FAIL **'} {sup}: {len(sub)} lines mapped")
    if not ok: failures.append(sup)

print(f"\nConsolidated {len(ROWS)} lines from {len(FILES)} suppliers, period {PERIOD}")
if failures:
    print("VALIDATION FAILURES:", failures, "- fix row_map/expected before building master"); sys.exit(1)
pickle.dump(ROWS, open(f"{WORKDIR}/consolidated_rows.pkl","wb"))
