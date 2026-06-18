"""Analytics-ready captured-invoice Excel + the 'all transactions included' guarantees."""
import io

from openpyxl import load_workbook

import capture_excel


def _draft(n_lines):
    lines = []
    for i in range(n_lines):
        lines.append({"invoice_no": "INV1", "date": "2026-05-01", "country": "Germany",
                      "net": 100.0, "vat": 19.0, "gross": 119.0, "currency": "EUR",
                      "supplier_name": "W.A.G. DE GmbH", "supplier_vat": "DE811",
                      "product": "Diesel", "qty": 100.0})
    return {"supplier": "W.A.G. a.s.", "supplier_vat": "CZ1", "statement_ref": "INV1",
            "statement_date": "2026-05-31", "currency": "EUR", "lines": lines}


def test_separated_overview_percountry_transactions():
    # Overview (header) and Per-country (table) are SEPARATE pages, then Transactions,
    # then a sheet per country (_draft is single-country Germany).
    wb = load_workbook(io.BytesIO(capture_excel.build(_draft(3))))
    assert wb.sheetnames[:3] == ["Overview", "Per-country", "Transactions"]
    assert "Germany" in wb.sheetnames


def test_one_sheet_per_country_with_overview():
    d = {"supplier": "S", "lines": [
        {"country": "Germany", "net": 100, "vat": 19, "gross": 119},
        {"country": "Poland", "net": 80, "vat": 18.4, "gross": 98.4},
        {"country": "Germany", "net": 50, "vat": 9.5, "gross": 59.5},
    ]}
    wb = load_workbook(io.BytesIO(capture_excel.build(d)))
    assert "Germany" in wb.sheetnames and "Poland" in wb.sheetnames
    # the Germany sheet aggregates ITS lines (2) and lists them
    rows = list(wb["Germany"].iter_rows(values_only=True))
    assert any(r[0] == "Transactions" and r[1] == 2 for r in rows if r and r[0])
    # header row + 2 transaction rows somewhere on the sheet
    assert sum(1 for r in rows if r and r[0] == "Germany" and len(r) > 5) == 0  # no stray
    assert wb["Germany"].max_row >= 11   # overview (8) + blank + header + 2 lines


def test_all_transactions_included_no_truncation():
    # every line must appear — header row + N data rows
    for n in (1, 25, 250):
        wb = load_workbook(io.BytesIO(capture_excel.build(_draft(n))))
        assert wb["Transactions"].max_row == n + 1, f"{n} lines must all be in the sheet"


def test_numbers_are_numeric_not_text():
    wb = load_workbook(io.BytesIO(capture_excel.build(_draft(2))))
    ws = wb["Transactions"]
    headers = [c.value for c in ws[1]]
    net_col = headers.index("Net") + 1
    v = ws.cell(row=2, column=net_col).value
    assert isinstance(v, (int, float)), "Net must be a real number for analytics"


def test_per_country_totals_reconcile():
    wb = load_workbook(io.BytesIO(capture_excel.build(_draft(3))))
    ws = wb["Per-country"]
    rows = list(ws.iter_rows(values_only=True))
    total = [r for r in rows if r and r[0] == "All countries"][0]
    assert total[1] == 3                 # 3 lines
    assert abs(total[2] - 300.0) < 0.01  # net 3*100
    assert abs(total[3] - 57.0) < 0.01   # vat 3*19


def test_per_line_entity_in_transactions():
    wb = load_workbook(io.BytesIO(capture_excel.build(_draft(1))))
    ws = wb["Transactions"]
    headers = [c.value for c in ws[1]]
    assert "Supply entity" in headers and "Supply VAT no" in headers
    ent_col = headers.index("Supply entity") + 1
    assert ws.cell(row=2, column=ent_col).value == "W.A.G. DE GmbH"


def test_empty_draft_does_not_crash():
    wb = load_workbook(io.BytesIO(capture_excel.build({"lines": []})))
    assert wb["Transactions"].max_row == 1   # header only


def test_route_serves_xlsx(client):
    import app
    # stash a draft for a token, then download
    app._stash_draft("xltok", _draft(2))
    r = client.get("/extract/capture.xlsx?token=xltok")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("Content-Type", "")
    wb = load_workbook(io.BytesIO(r.data))
    assert wb["Transactions"].max_row == 3
