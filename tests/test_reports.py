"""Tests for the polished Excel reports (summary + styled exports)."""
import os


def test_summary_workbook_structure(tmp_path):
    import reports
    from openpyxl import load_workbook
    path = reports.summary_workbook(path=str(tmp_path / "sum.xlsx"))
    assert os.path.exists(path)
    wb = load_workbook(path)
    # all the expected sheets are present
    for sheet in ("Summary", "By supplier", "By country", "By entity (VAT)", "Trend", "Savings"):
        assert sheet in wb.sheetnames
    ws = wb["Summary"]
    assert "Fleet Fuel Report" in str(ws["A1"].value)
    assert len(ws._charts) >= 1               # a chart was added
    # KPI value cells are numeric
    assert isinstance(ws.cell(4, 3).value, (int, float))


def test_export_summary_route(client):
    r = client.get("/export/summary")
    assert r.status_code == 200
    assert r.get_data()[:2] == b"PK"          # valid xlsx (zip)


def test_export_compare_styled(client):
    r = client.get("/export/compare?period=2026-05")
    assert r.status_code == 200 and r.get_data()[:2] == b"PK"
