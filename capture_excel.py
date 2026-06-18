"""
capture_excel.py — the CLEAR, analytics-ready captured-invoice file.

Turns a review DRAFT (vision capture OR structured e-invoice — anything with `lines`)
into a typed .xlsx so nothing is ambiguous and any analytics tool (Excel pivots, pandas,
Power BI) reads it directly. Two sheets, MAIN DATA FIRST:

  1. Summary       — the main data at a glance: invoice header (supplier, customer, number,
                     dates, currency), the computed totals, and the PER-COUNTRY breakdown
                     (net / VAT / gross + supplying entities) — each country is a refund
                     jurisdiction.
  2. Transactions  — ALL cleaned line items, ONE row per fuel/toll transaction, every field
                     in its own typed column, including the per-country ENTITY OF SUPPLY.

Pure + best-effort: reads the draft, invents no figure (sums via money.fsum), never mutates
anything, returns the .xlsx bytes. Amounts are NET-basis EUR as captured. EVERY captured
transaction is included — there is no row cap.
"""
import io

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

import money

_HDR_FILL = PatternFill("solid", fgColor="0E5FA8")
_HDR_FONT = Font(bold=True, color="FFFFFF")
_TOT_FONT = Font(bold=True)

# (column title, draft-line key, is_numeric)
_TX_COLS = [
    ("Invoice no", "invoice_no", False),
    ("Date", "date", False),
    ("Time", "time", False),
    ("Country", "country", False),
    ("Supply entity", "supplier_name", False),
    ("Supply VAT no", "supplier_vat", False),
    ("Station", "station_name", False),
    ("City", "city", False),
    ("Product", "product", False),
    ("Qty", "qty", True),
    ("Unit", "unit", False),
    ("Unit price", "unit_price", True),
    ("Discount", "discount", True),
    ("Net", "net", True),
    ("VAT %", "vat_rate", True),
    ("VAT", "vat", True),
    ("Gross", "gross", True),
    ("Currency", "currency", False),
    ("Card no", "card_no", False),
    ("Receipt no", "receipt_no", False),
]
_MONEY_KEYS = ("net", "vat", "gross", "discount", "unit_price")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _style_header(ws, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = _HDR_FILL
        cell.font = _HDR_FONT
    ws.freeze_panes = "A2"


def _autowidth(ws, ncols, cap=42):
    from openpyxl.utils import get_column_letter
    for c in range(1, ncols + 1):
        width = 8
        for row in ws.iter_rows(min_col=c, max_col=c):
            v = row[0].value
            if v is not None:
                width = max(width, min(cap, len(str(v)) + 2))
        ws.column_dimensions[get_column_letter(c)].width = width


def build(draft, source_name=None):
    """Return the .xlsx bytes for a captured draft (Summary sheet first, then Transactions)."""
    draft = draft or {}
    lines = [l for l in (draft.get("lines") or []) if isinstance(l, dict)]
    hdr_supplier = draft.get("supplier")
    hdr_supplier_vat = draft.get("supplier_vat")
    currency = draft.get("currency") or "EUR"

    # ---- aggregate per country (drives the Summary) ----------------------------------
    by = {}
    for ln in lines:
        ctry = (ln.get("country") or "—").strip() or "—"
        a = by.setdefault(ctry, {"n": 0, "net": [], "vat": [], "gross": [], "ents": set()})
        a["n"] += 1
        a["net"].append(_num(ln.get("net")) or 0)
        a["vat"].append(_num(ln.get("vat")) or 0)
        g = _num(ln.get("gross"))
        a["gross"].append(g if g is not None
                          else (_num(ln.get("net")) or 0) + (_num(ln.get("vat")) or 0))
        ent = (ln.get("supplier_name") or hdr_supplier or "").strip()
        if ent:
            a["ents"].add(ent)
    country_rows, tnet, tvat, tgross = [], 0.0, 0.0, 0.0
    for ctry in sorted(by):
        a = by[ctry]
        net = float(money.fsum(a["net"])); vat = float(money.fsum(a["vat"]))
        gross = float(money.fsum(a["gross"]))
        tnet += net; tvat += vat; tgross += gross
        country_rows.append([ctry, a["n"], net, vat, gross, ", ".join(sorted(a["ents"]))])

    wb = Workbook()

    # ===== Sheet 1: SUMMARY (the main data, first) ====================================
    ws = wb.active
    ws.title = "Summary"
    ws.append(["CAPTURED INVOICE — SUMMARY"])
    ws.cell(row=1, column=1).font = Font(bold=True, size=13)
    for k, v in [
        ("Source file", source_name or ""),
        ("Supplier (header)", hdr_supplier or ""),
        ("Supplier VAT (header)", hdr_supplier_vat or ""),
        ("Customer", draft.get("customer") or ""),
        ("Invoice / statement no", draft.get("statement_ref") or ""),
        ("Statement date", draft.get("statement_date") or ""),
        ("Currency", currency),
        ("Transactions captured", len(lines)),
        ("Total net", float(money.f2(tnet))),
        ("Total VAT", float(money.f2(tvat))),
        ("Total gross", float(money.f2(tgross))),
        ("Basis", "NET EUR, VAT-excluded (VAT shown separately)"),
    ]:
        ws.append([k, v])
        ws.cell(row=ws.max_row, column=1).font = _TOT_FONT
    if draft.get("_pages_truncated"):
        t = draft["_pages_truncated"]
        ws.append(["⚠ INCOMPLETE",
                   f"only {t.get('read')} of {t.get('total')} pages were read — "
                   "later-page transactions are missing"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, color="C0392B")
    ws.append([])
    hrow = ws.max_row + 1
    ws.append(["Per country", "Lines", "Net", "VAT", "Gross", "Supply entities"])
    for c in range(1, 7):
        cell = ws.cell(row=hrow, column=c); cell.fill = _HDR_FILL; cell.font = _HDR_FONT
    for r in country_rows:
        ws.append(r)
    if country_rows:
        ws.append(["All countries", len(lines), float(money.f2(tnet)),
                   float(money.f2(tvat)), float(money.f2(tgross)), ""])
        for c in range(1, 7):
            ws.cell(row=ws.max_row, column=c).font = _TOT_FONT
    ws.column_dimensions["A"].width = 26
    for col in ("B", "C", "D", "E"):
        ws.column_dimensions[col].width = 15
    ws.column_dimensions["F"].width = 44

    # ===== Sheet 2: TRANSACTIONS (all cleaned line items) =============================
    ws2 = wb.create_sheet("Transactions")
    ws2.append([c[0] for c in _TX_COLS])
    for ln in lines:
        rowvals = []
        for _title, key, is_num in _TX_COLS:
            v = ln.get(key)
            if key == "supplier_name" and not v:
                v = hdr_supplier
            elif key == "supplier_vat" and not v:
                v = hdr_supplier_vat
            elif key == "currency" and not v:
                v = currency
            if is_num:
                nval = _num(v)
                v = (float(money.f2(nval)) if key in _MONEY_KEYS and nval is not None else nval)
            rowvals.append(v)
        ws2.append(rowvals)
    _style_header(ws2, len(_TX_COLS))
    _autowidth(ws2, len(_TX_COLS))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()
