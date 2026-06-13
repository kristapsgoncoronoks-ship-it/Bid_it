"""
CONSOLIDATION ENGINE - generic; never edited when suppliers are added.
Reads supplier_specs.py (the trained registry) + month_config.py, maps every
workbook line to the canonical schema, VALIDATES each supplier against its
'expected' invoice figures, and pickles the rows for build_master.py.
Run:  python3 consolidate.py

IMPORTING this module is SIDE-EFFECT-FREE: the consolidation runs only when you
call run() (the engine orchestrator) or execute the module as __main__ (the
standalone CLI). The pickle written by run() is PERIOD-STAMPED (see _dump_pickle)
so a downstream step can ASSERT it is reading rows for the period it expects.
"""
import hashlib, pickle, sys
import money
from supplier_specs import SPECS, ROW_MAPS, prod_group
from month_config import FX, FILES
import month_config
from ingest import fetch_records

import os
WORKDIR = os.path.dirname(os.path.abspath(__file__))
PICKLE = f"{WORKDIR}/consolidated_rows.pkl"

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


def rows_sha256(rows):
    """Stable content hash over the canonical rows (order-sensitive) — the close
    asserts this matches what consolidate wrote, catching an in-place pickle edit."""
    h = hashlib.sha256()
    h.update(repr(rows).encode("utf-8"))
    return h.hexdigest()


def _dump_pickle(rows, period, path=PICKLE):
    """Write the period-STAMPED pickle: a dict carrying {period, count, sha256, rows}.
    build_master.build()/history.load() read this and assert period/count/sha before
    loading, so editing month_config between steps can't stamp the new period onto old
    rows (reliability finding #3)."""
    payload = {"period": period, "count": len(rows),
               "sha256": rows_sha256(rows), "rows": rows}
    with open(path, "wb") as f:
        pickle.dump(payload, f)
    return payload


def load_rows(period, path=PICKLE):
    """Read the period-stamped pickle and RETURN its rows, asserting it was written
    for `period` (and that count/sha match) BEFORE the caller loads anything. This is
    the kill for reliability finding #3: a stale pickle from a different month raises
    a clear RuntimeError instead of silently stamping the wrong period onto old rows.

    Back-compat: an OLD bare-list pickle (pre-D5, no period stamp) is tolerated with a
    warning — we cannot verify its period, so we trust the caller's request and load it.
    """
    import applog
    with open(path, "rb") as f:
        payload = pickle.load(f)
    if isinstance(payload, dict) and "rows" in payload:
        if payload.get("period") != period:
            raise RuntimeError(
                f"pickle period {payload.get('period')!r} != requested {period!r} "
                f"— re-run consolidate")
        rows = payload["rows"]
        if payload.get("count") != len(rows):
            raise RuntimeError(
                f"pickle count {payload.get('count')} != actual {len(rows)} "
                f"— re-run consolidate")
        if payload.get("sha256") != rows_sha256(rows):
            raise RuntimeError(
                "pickle sha256 mismatch (rows changed in place) — re-run consolidate")
        return rows
    # legacy bare-list pickle: no period stamp to verify.
    applog.get("consolidate").warning(
        "consolidated_rows.pkl is a legacy bare-list pickle (no period stamp); "
        "loading for requested period %s unverified — re-run consolidate to upgrade",
        period)
    return list(payload)


def run(period=None):
    """Consolidate every supplier workbook for `period` (default month_config.PERIOD)
    into the canonical schema, VALIDATE each against its 'expected' figures, and write
    the period-stamped pickle. Returns the canonical ROWS list.

    Raises SystemExit(1) on a validation failure (the standalone CLI contract) — the
    orchestrator treats that as a hard halt with a clear message.
    """
    period = period or month_config.PERIOD
    ctx = {"fx": FX, "period": period}
    rows, failures = [], []
    for sup, fname in FILES.items():
        if sup not in SPECS or sup not in ROW_MAPS:
            print(f"!! {sup}: no spec/row_map registered - onboard it in supplier_specs.py"); failures.append(sup); continue
        spec = SPECS[sup]; ent, _, _ = spec["entity"]
        n0 = len(rows)
        for r in fetch_records(sup, spec, ctx, files=FILES):
            m = ROW_MAPS[sup](r, ctx)
            if m is None: continue
            rows.append([ent, sup, m.get("country", spec["country"]), m["vehicle"], norm_date(m["date"]),
                         m.get("time",""), m["station"], m["product"], prod_group(m["product"]),
                         m["qty"], m.get("currency", spec["currency"]),
                         money.f2(m["net_local"]), money.f2(m["vat_local"]), money.f2(m["gross_local"]),
                         money.f2(m["net_eur"]), money.f2(m["vat_eur"]),
                         money.f2(m.get("net_eur_eff", m["net_eur"])), m.get("note","")])
        sub = rows[n0:]
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

    print(f"\nConsolidated {len(rows)} lines from {len(FILES)} suppliers, period {period}")
    if failures:
        print("VALIDATION FAILURES:", failures, "- fix row_map/expected before building master")
        sys.exit(1)
    _dump_pickle(rows, period)
    return rows


# consolidate (the verb) is an alias — both names appear in docs/the orchestrator.
consolidate = run


if __name__ == "__main__":
    run()
