"""
EXCEL REPORTING - polished workbooks from fuel_history.db.

Reusable styling helpers (styled headers, number formats, banded rows, totals,
conditional colour scales, charts, freeze panes) plus a one-click executive
SUMMARY report. All prices are NET EUR/L, final (VAT excluded, rebates applied).

    python3 reports.py [period]      -> Fleet_Fuel_Summary_<period>.xlsx
"""
import os
import sqlite3
import datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import ColorScaleRule, DataBarRule
from openpyxl.utils import get_column_letter

import money

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/fuel_history.db"

# palette
INK = "1A2733"; ACC = "0E5FA8"; OKG = "1B7340"; BADR = "C8102E"
HEADER_FILL = PatternFill("solid", fgColor=ACC)
TITLE_FILL = PatternFill("solid", fgColor=INK)
BAND_FILL = PatternFill("solid", fgColor="F2F6FA")
WHITE_BOLD = Font(bold=True, color="FFFFFF")
THIN = Side(style="thin", color="DDE4EA")
TOPB = Border(top=Side(style="thin", color="9FB3C4"))

FMT_INT = "#,##0"
FMT_EUR = "#,##0.00"
FMT_PRICE = "#,##0.0000"
FMT_PCT = "0.0%"


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------- styling
def style_header(ws, row=1, ncols=None):
    ncols = ncols or ws.max_column
    for c in range(1, ncols + 1):
        cell = ws.cell(row, c)
        cell.font = WHITE_BOLD
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 26


def band_rows(ws, first, last, ncols):
    for r in range(first, last + 1):
        if (r - first) % 2:
            for c in range(1, ncols + 1):
                ws.cell(r, c).fill = BAND_FILL


def set_widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def number_format(ws, col, first, last, fmt):
    for r in range(first, last + 1):
        ws.cell(r, col).number_format = fmt


def totals_row(ws, row, ncols, label_col=1):
    for c in range(1, ncols + 1):
        cell = ws.cell(row, c)
        cell.font = Font(bold=True)
        cell.border = TOPB
    ws.cell(row, label_col).font = Font(bold=True)


# ---------------------------------------------------------------- data
def _periods(con):
    return [r[0] for r in con.execute("SELECT DISTINCT period FROM transactions ORDER BY period DESC")]

def _kpis(con, period):
    r = con.execute("""
        SELECT ROUND(SUM(net_eur),2) net, ROUND(SUM(vat_eur),2) vat,
               ROUND(SUM(net_eur)+SUM(vat_eur),2) gross, COUNT(*) lines,
               (SELECT ROUND(SUM(qty),0) FROM transactions WHERE period=? AND product_group='Diesel') dl,
               (SELECT ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) FROM transactions
                  WHERE period=? AND product_group='Diesel') eurl
        FROM transactions WHERE period=?""", (period, period, period)).fetchone()
    return r

def _by(con, period, dim):
    return con.execute(f"""
        SELECT {dim} k, ROUND(SUM(qty),0) litres, ROUND(SUM(net_eur),2) net,
               ROUND(SUM(vat_eur),2) vat,
               ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) eurl
        FROM transactions WHERE period=? GROUP BY {dim} ORDER BY net DESC""", (period,)).fetchall()

def _by_entity(con, period):
    return con.execute("""
        SELECT entity k, country, ROUND(SUM(net_eur),2) net, ROUND(SUM(vat_eur),2) vat,
               ROUND(SUM(net_eur)+SUM(vat_eur),2) gross
        FROM transactions WHERE period=? GROUP BY entity, country ORDER BY net DESC""",
        (period,)).fetchall()

def _trend(con):
    return con.execute("""
        SELECT period, ROUND(SUM(qty),0) litres,
               ROUND(SUM(net_eur),2) net,
               ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) eurl
        FROM transactions WHERE product_group='Diesel' GROUP BY period ORDER BY period""").fetchall()

def _savings(con, period):
    rows = con.execute("""
        SELECT date, country, supplier, SUM(qty) q, SUM(net_eur_eff) e
        FROM transactions WHERE product_group='Diesel' AND period=?
        GROUP BY date, country, supplier""", (period,)).fetchall()
    g = {}
    for r in rows:
        if r["q"]:
            g.setdefault((r["date"], r["country"]), {})[r["supplier"]] = (r["q"], r["e"])
    by_sup, by_ctry, total = {}, {}, 0.0
    for (_d, c), bysup in g.items():
        if len(bysup) < 2:
            continue
        prices = {s: e / qy for s, (qy, e) in bysup.items()}
        cheap = min(prices.values())
        for s, (qy, _e) in bysup.items():
            over = qy * (prices[s] - cheap)
            if over <= 0:
                continue
            total += over
            by_sup[s] = by_sup.get(s, 0) + over
            by_ctry[c] = by_ctry.get(c, 0) + over
    return {"total": round(total, 2),
            "by_sup": sorted(by_sup.items(), key=lambda x: -x[1]),
            "by_ctry": sorted(by_ctry.items(), key=lambda x: -x[1])}


# ---------------------------------------------------------------- summary report
def summary_workbook(period=None, path=None):
    con = connect()
    ps = _periods(con)
    period = period or (ps[0] if ps else None)
    if period is None:
        con.close()
        raise ValueError("no data loaded — nothing to report")
    k = _kpis(con, period)
    sv = _savings(con, period)
    wb = Workbook()
    _sheet_summary(wb, period, k, sv,
                   _by(con, period, "supplier"), _by(con, period, "country"))
    _sheet_table(wb, "By supplier", _by(con, period, "supplier"), period, chart=True)
    _sheet_table(wb, "By country", _by(con, period, "country"), period)
    _sheet_entity(wb, _by_entity(con, period), period)
    _sheet_trend(wb, _trend(con))
    _sheet_savings(wb, sv, period)
    con.close()
    path = path or os.path.join(WORKDIR, f"Fleet_Fuel_Summary_{period}.xlsx")
    wb.save(path)
    return path


def _title(ws, period, subtitle, span="A1:F1"):
    ws.merge_cells(span)
    t = ws.cell(1, 1, f"Fleet Fuel Report — {period}")
    t.font = Font(bold=True, color="FFFFFF", size=16)
    t.fill = TITLE_FILL
    t.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 32
    last_col = span.split(":")[1][0]
    ws.merge_cells(f"A2:{last_col}2")
    s = ws.cell(2, 1, subtitle)
    s.font = Font(italic=True, color="5B6B7A", size=9)
    s.alignment = Alignment(horizontal="left", indent=1)


def _kpi_card(ws, row, col, value, label, fmt=FMT_EUR, accent=ACC):
    v = ws.cell(row, col, value)
    v.font = Font(bold=True, size=15, color=accent)
    v.number_format = fmt
    v.alignment = Alignment(horizontal="left")
    lab = ws.cell(row + 1, col, label)
    lab.font = Font(size=9, color="5B6B7A")
    for r in (row, row + 1):
        ws.cell(r, col).border = Border(left=Side(style="thick", color=accent))


def _sheet_summary(wb, period, k, sv, by_sup, by_ctry):
    ws = wb.active; ws.title = "Summary"
    gen = datetime.date.today().isoformat()
    _title(ws, period, f"NET EUR/L, final (VAT excluded, rebates applied) · generated {gen}", "A1:F1")
    set_widths(ws, [24, 16, 16, 16, 16, 16])

    # KPI cards (row 4 value / row 5 label)
    cards = [
        (f"{(k['dl'] or 0):,.0f}", "Diesel litres", FMT_INT, ACC),
        (k["eurl"] or 0, "Fleet eff. €/L", FMT_PRICE, ACC),
        (k["net"] or 0, "Net spend (EUR)", FMT_EUR, INK),
        (k["vat"] or 0, "Reclaimable VAT (EUR)", FMT_EUR, OKG),
        (sv["total"], "Avoidable overpay (EUR)", FMT_EUR, BADR),
    ]
    for i, (val, lab, fmt, acc) in enumerate(cards):
        # litres value is a string already formatted; keep numeric where possible
        if isinstance(val, str):
            c = ws.cell(4, i + 1, k["dl"] or 0); c.number_format = FMT_INT
            c.font = Font(bold=True, size=15, color=acc)
            ws.cell(5, i + 1, lab).font = Font(size=9, color="5B6B7A")
            for r in (4, 5):
                ws.cell(r, i + 1).border = Border(left=Side(style="thick", color=acc))
        else:
            _kpi_card(ws, 4, i + 1, val, lab, fmt, acc)

    # mini table: top suppliers by net spend
    r0 = 8
    ws.cell(r0, 1, "Spend by supplier").font = Font(bold=True, size=11)
    hdr = ["Supplier", "Litres", "Net EUR", "VAT EUR", "€/L eff", "Share"]
    for j, h in enumerate(hdr, 1):
        ws.cell(r0 + 1, j, h)
    style_header(ws, r0 + 1, len(hdr))
    tot_net = sum((r["net"] or 0) for r in by_sup) or 1
    rr = r0 + 2
    for r in by_sup:
        ws.cell(rr, 1, r["k"])
        ws.cell(rr, 2, r["litres"] or 0).number_format = FMT_INT
        ws.cell(rr, 3, r["net"] or 0).number_format = FMT_EUR
        ws.cell(rr, 4, r["vat"] or 0).number_format = FMT_EUR
        ws.cell(rr, 5, r["eurl"] or 0).number_format = FMT_PRICE
        ws.cell(rr, 6, (r["net"] or 0) / tot_net).number_format = FMT_PCT
        rr += 1
    band_rows(ws, r0 + 2, rr - 1, len(hdr))
    # totals
    ws.cell(rr, 1, "TOTAL")
    ws.cell(rr, 2, f"=SUM(B{r0+2}:B{rr-1})").number_format = FMT_INT
    ws.cell(rr, 3, f"=SUM(C{r0+2}:C{rr-1})").number_format = FMT_EUR
    ws.cell(rr, 4, f"=SUM(D{r0+2}:D{rr-1})").number_format = FMT_EUR
    ws.cell(rr, 6, 1).number_format = FMT_PCT
    totals_row(ws, rr, len(hdr))
    # conditional colour scale on €/L (green cheap -> red dear) + data bars on litres
    ws.conditional_formatting.add(f"E{r0+2}:E{rr-1}",
        ColorScaleRule(start_type="min", start_color="63BE7B",
                       mid_type="percentile", mid_value=50, mid_color="FFEB84",
                       end_type="max", end_color="F8696B"))
    ws.conditional_formatting.add(f"B{r0+2}:B{rr-1}",
        DataBarRule(start_type="min", end_type="max", color="9CC3E6"))

    # bar chart: net spend by supplier
    ch = BarChart(); ch.type = "col"; ch.title = "Net spend by supplier (EUR)"
    ch.height = 7.5; ch.width = 16; ch.legend = None
    data = Reference(ws, min_col=3, min_row=r0 + 1, max_row=rr - 1)
    cats = Reference(ws, min_col=1, min_row=r0 + 2, max_row=rr - 1)
    ch.add_data(data, titles_from_data=True); ch.set_categories(cats)
    ws.add_chart(ch, f"H{r0+1}")
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A3"


def _sheet_table(wb, title, rows, period, chart=False):
    ws = wb.create_sheet(title)
    hdr = [title.replace("By ", "").capitalize(), "Litres", "Net EUR", "VAT EUR", "€/L eff"]
    for j, h in enumerate(hdr, 1):
        ws.cell(1, j, h)
    style_header(ws, 1, len(hdr))
    rr = 2
    for r in rows:
        ws.cell(rr, 1, r["k"])
        ws.cell(rr, 2, r["litres"] or 0).number_format = FMT_INT
        ws.cell(rr, 3, r["net"] or 0).number_format = FMT_EUR
        ws.cell(rr, 4, r["vat"] or 0).number_format = FMT_EUR
        ws.cell(rr, 5, r["eurl"] or 0).number_format = FMT_PRICE
        rr += 1
    band_rows(ws, 2, rr - 1, len(hdr))
    ws.cell(rr, 1, "TOTAL")
    ws.cell(rr, 2, f"=SUM(B2:B{rr-1})").number_format = FMT_INT
    ws.cell(rr, 3, f"=SUM(C2:C{rr-1})").number_format = FMT_EUR
    ws.cell(rr, 4, f"=SUM(D2:D{rr-1})").number_format = FMT_EUR
    totals_row(ws, rr, len(hdr))
    ws.conditional_formatting.add(f"E2:E{rr-1}",
        ColorScaleRule(start_type="min", start_color="63BE7B",
                       mid_type="percentile", mid_value=50, mid_color="FFEB84",
                       end_type="max", end_color="F8696B"))
    set_widths(ws, [22, 14, 16, 14, 12])
    ws.freeze_panes = "A2"; ws.sheet_view.showGridLines = False
    if chart:
        ch = BarChart(); ch.type = "bar"; ch.title = f"{title} — net spend"
        ch.height = 8; ch.width = 14; ch.legend = None
        data = Reference(ws, min_col=3, min_row=1, max_row=rr - 1)
        cats = Reference(ws, min_col=1, min_row=2, max_row=rr - 1)
        ch.add_data(data, titles_from_data=True); ch.set_categories(cats)
        ws.add_chart(ch, "G2")


def _sheet_entity(wb, rows, period):
    ws = wb.create_sheet("By entity (VAT)")
    hdr = ["Entity", "Country", "Net EUR", "VAT reclaimable", "Gross EUR"]
    for j, h in enumerate(hdr, 1):
        ws.cell(1, j, h)
    style_header(ws, 1, len(hdr))
    rr = 2
    for r in rows:
        ws.cell(rr, 1, r["k"]); ws.cell(rr, 2, r["country"])
        ws.cell(rr, 3, r["net"] or 0).number_format = FMT_EUR
        ws.cell(rr, 4, r["vat"] or 0).number_format = FMT_EUR
        ws.cell(rr, 5, r["gross"] or 0).number_format = FMT_EUR
        rr += 1
    band_rows(ws, 2, rr - 1, len(hdr))
    ws.cell(rr, 1, "TOTAL")
    for col, L in ((3, "C"), (4, "D"), (5, "E")):
        ws.cell(rr, col, f"=SUM({L}2:{L}{rr-1})").number_format = FMT_EUR
    totals_row(ws, rr, len(hdr))
    set_widths(ws, [26, 14, 16, 16, 16])
    ws.freeze_panes = "A2"; ws.sheet_view.showGridLines = False


def _sheet_trend(wb, rows):
    ws = wb.create_sheet("Trend")
    hdr = ["Period", "Diesel litres", "Net EUR", "Fleet eff. €/L"]
    for j, h in enumerate(hdr, 1):
        ws.cell(1, j, h)
    style_header(ws, 1, len(hdr))
    rr = 2
    for r in rows:
        ws.cell(rr, 1, r["period"])
        ws.cell(rr, 2, r["litres"] or 0).number_format = FMT_INT
        ws.cell(rr, 3, r["net"] or 0).number_format = FMT_EUR
        ws.cell(rr, 4, r["eurl"] or 0).number_format = FMT_PRICE
        rr += 1
    set_widths(ws, [12, 14, 16, 14])
    ws.freeze_panes = "A2"; ws.sheet_view.showGridLines = False
    if rr > 3:
        ch = LineChart(); ch.title = "Fleet effective €/L trend"; ch.height = 8; ch.width = 16
        data = Reference(ws, min_col=4, min_row=1, max_row=rr - 1)
        cats = Reference(ws, min_col=1, min_row=2, max_row=rr - 1)
        ch.add_data(data, titles_from_data=True); ch.set_categories(cats)
        ws.add_chart(ch, "F2")


def _sheet_savings(wb, sv, period):
    ws = wb.create_sheet("Savings")
    _title(ws, period, "Avoidable overpay vs the cheapest same-day, same-country rival (diesel)", "A1:D1")
    set_widths(ws, [22, 16, 4, 0])
    ws.cell(4, 1, "Total avoidable overpay (EUR)").font = Font(bold=True)
    tc = ws.cell(4, 2, sv["total"]); tc.number_format = FMT_EUR
    tc.font = Font(bold=True, size=13, color=BADR)
    for start_row, title, items in ((7, "Overpay by supplier", sv["by_sup"]),
                                    (7 + len(sv["by_sup"]) + 4, "Overpay by country", sv["by_ctry"])):
        ws.cell(start_row, 1, title).font = Font(bold=True, size=11)
        ws.cell(start_row + 1, 1, "Name"); ws.cell(start_row + 1, 2, "Overpay EUR")
        style_header(ws, start_row + 1, 2)
        r = start_row + 2
        for name, val in items:
            ws.cell(r, 1, name)
            ws.cell(r, 2, money.f2(val)).number_format = FMT_EUR
            r += 1
        band_rows(ws, start_row + 2, r - 1, 2)
        if r > start_row + 2:
            ws.conditional_formatting.add(f"B{start_row+2}:B{r-1}",
                DataBarRule(start_type="min", end_type="max", color="F4A6A6"))
    ws.freeze_panes = "A3"; ws.sheet_view.showGridLines = False


def fee_report_workbook(claim, path=None):
    """One-sheet service-fee calculation for a single VAT claim. `claim` is a
    vat_applications row (dict). Fee = % of the refunded amount, or the minimum per
    claim when the % is lower."""
    from openpyxl.styles import Font
    ent = claim.get("entity") or ""
    ctry = claim.get("refund_country") or claim.get("country") or ""
    per = claim.get("ref_period") or claim.get("period") or ""
    amount = (claim.get("paid_amount") or claim.get("vat_eur") or 0) or 0
    pct = claim.get("fee_pct") or 0
    mn = money.f2(claim.get("fee_min") or 0)
    pct_fee = money.f2(pct / 100.0 * amount)
    fee = claim.get("fee_eur")
    fee = money.f2(fee) if fee is not None else max(pct_fee, mn)
    basis = "percent (% of refund)" if pct_fee >= mn else "minimum per claim"
    wb = Workbook(); ws = wb.active; ws.title = "Fee calculation"
    _title(ws, per, f"Service fee calculation — {ent} / {ctry}", "A1:C1")
    set_widths(ws, [34, 18, 4])
    lines = [
        ("Customer", ent, None), ("Refund country", ctry, None),
        ("Claim period", per, None), ("Status", claim.get("status") or "", None),
        ("", "", None),
        ("Refunded VAT amount (EUR)", amount, FMT_EUR),
        (f"Fee at contract rate ({pct:g}%)", pct_fee, FMT_EUR),
        ("Minimum fee per claim (EUR)", mn, FMT_EUR),
        ("FEE CHARGED (EUR)", fee, FMT_EUR),
        ("Basis", basis, None),
        ("", "", None),
        ("Submitted", claim.get("submitted_date") or claim.get("submitted") or "", None),
        ("Refund paid", claim.get("paid_date") or claim.get("paid") or "", None),
        ("Fee billed", claim.get("fee_billed_date") or "(charged when refund is paid)", None),
    ]
    r0 = 4
    for i, (lbl, val, fmt) in enumerate(lines):
        rr = r0 + i
        c1 = ws.cell(rr, 1, lbl)
        c2 = ws.cell(rr, 2, val if val is not None else "")
        if fmt:
            c2.number_format = fmt
        if str(lbl).startswith("FEE CHARGED"):
            c1.font = Font(bold=True, size=12)
            c2.font = Font(bold=True, size=12, color=ACC)
    ws.sheet_view.showGridLines = False
    safe = f"Fee_{ent}_{ctry}_{per}".replace(" ", "_")
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in safe)
    path = path or os.path.join(WORKDIR, safe + ".xlsx")
    wb.save(path)
    return path


def claims_overview_workbook(overview, year, path=None):
    """VAT-refund submission readiness: 'Ready to submit' + 'Open claims' sheets."""
    wb = Workbook(); first = True
    # ready-to-submit
    ws = wb.active; ws.title = "Ready to submit"
    hdr = ["Entity", "Country", "Period", "VAT EUR", "Can submit?", "Blocking reasons"]
    for j, h in enumerate(hdr, 1):
        ws.cell(1, j, h)
    style_header(ws, 1, len(hdr))
    rr = 2
    for c in overview["to_submit"]:
        ws.cell(rr, 1, c["entity"]); ws.cell(rr, 2, c["country"]); ws.cell(rr, 3, c["period"])
        ws.cell(rr, 4, c["vat_eur"] or 0).number_format = FMT_EUR
        cell = ws.cell(rr, 5, "READY" if c["ready"] else "BLOCKED")
        cell.font = Font(bold=True, color=OKG if c["ready"] else BADR)
        ws.cell(rr, 6, "; ".join(c["issues"]))
        rr += 1
    band_rows(ws, 2, rr - 1, len(hdr))
    set_widths(ws, [24, 14, 12, 14, 12, 50]); ws.freeze_panes = "A2"; ws.sheet_view.showGridLines = False
    # open claims
    ws2 = wb.create_sheet("Open claims")
    hdr2 = ["Entity", "Country", "Period", "VAT EUR", "Status", "Submitted", "Age (days)"]
    for j, h in enumerate(hdr2, 1):
        ws2.cell(1, j, h)
    style_header(ws2, 1, len(hdr2))
    rr = 2
    for c in overview["open"]:
        ws2.cell(rr, 1, c["entity"]); ws2.cell(rr, 2, c["country"]); ws2.cell(rr, 3, c["period"])
        ws2.cell(rr, 4, c["vat_eur"] or 0).number_format = FMT_EUR
        ws2.cell(rr, 5, c["status"]); ws2.cell(rr, 6, c["submitted"] or "")
        ws2.cell(rr, 7, c["age_days"] if c["age_days"] != "" else "")
        rr += 1
    band_rows(ws2, 2, rr - 1, len(hdr2))
    set_widths(ws2, [24, 14, 12, 14, 12, 13, 11]); ws2.freeze_panes = "A2"; ws2.sheet_view.showGridLines = False
    path = path or os.path.join(WORKDIR, f"VAT_Claim_Readiness_{year}.xlsx")
    wb.save(path)
    return path


if __name__ == "__main__":
    import sys
    per = sys.argv[1] if len(sys.argv) > 1 else None
    p = summary_workbook(per)
    print("wrote", p)
