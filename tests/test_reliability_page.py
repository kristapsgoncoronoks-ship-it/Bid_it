"""Web page for supplier RELIABILITY (slice R2) — the /reliability page that captures
advertised prices (upload xlsx/csv/xml/pdf + manual) and renders the per-supplier
overcharge / reliability analysis.

These exercise the ROUTE (app.py) on top of the frozen R1 engine
(pricing_intelligence.py). The benchmark/product DBs are redirected into a tmp dir so
the test owns its data and leaves no demo churn. transactions are seeded read-only.
"""
import importlib
import io
import sqlite3

import pytest


@pytest.fixture()
def rel_env(client, tmp_path, monkeypatch):
    """Logged-in client + a pricing_intelligence pointed at throwaway DBs, seeded with a
    couple of invoiced fills so the engine has something to match against."""
    import pricing_intelligence as PI
    fuel = str(tmp_path / "fuel_history.db")
    bench = str(tmp_path / "benchmark.db")
    monkeypatch.setattr(PI, "DB", fuel)
    monkeypatch.setattr(PI, "BENCHMARK_DB", bench)
    # the one-time legacy-import guard is keyed by BENCHMARK_DB path; clear it so the
    # fresh tmp benchmark.db migrates cleanly.
    PI._MIGRATED.discard(bench)
    con = sqlite3.connect(fuel)
    con.execute("""CREATE TABLE IF NOT EXISTS transactions (
        supplier TEXT, country TEXT, station TEXT, date TEXT, period TEXT,
        product_group TEXT, qty REAL, net_eur_eff REAL,
        tenant_id TEXT NOT NULL DEFAULT 'default')""")
    con.executemany(
        "INSERT INTO transactions (supplier,country,station,date,period,product_group,"
        "qty,net_eur_eff) VALUES (?,?,?,?,?,?,?,?)", [
            # Q8 Riga: invoiced 1.50 €/L (net 150 / 100 L) — above any 1.40 advert
            ("Q8", "LV", "Riga", "2026-05-10", "2026-05", "Diesel", 100.0, 150.0)])
    con.commit(); con.close()
    return PI


def test_get_renders_sections(rel_env, client):
    r = client.get("/reliability")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Supplier reliability" in body
    assert "Where you were overcharged" in body
    assert "Add advertised prices — upload" in body
    assert "Add advertised price — manual" in body
    assert "Recent advertised prices" in body


def test_chart_renders_when_overcharge_data(rel_env, client):
    # advertise 1.40 against the seeded 1.50 invoiced fill -> a real overcharge,
    # so the overcharge-by-supplier chart renders an <svg>.
    rel_env.add_advertised_price("Q8", "LV", "Riga", "2026-05-01", 1.40)
    body = client.get("/reliability").get_data(as_text=True)
    assert "<svg" in body


def _csrf(client, path="/reliability"):
    import re
    body = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


def test_manual_add_advertised_price(rel_env, client):
    tok = _csrf(client)
    r = client.post("/reliability", data={
        "_csrf": tok, "__act": "manual", "supplier": "Q8", "country": "LV",
        "city": "Riga", "date": "2026-05-01", "product_group": "Diesel",
        "net_price": "1.4000"})
    assert r.status_code == 200
    assert b"Added advertised price" in r.data
    stored = rel_env.list_advertised_prices()
    assert any(s["supplier"] == "Q8" and abs(s["net_price"] - 1.40) < 1e-9
               for s in stored)
    # an advertised 1.40 vs invoiced 1.50 surfaces an overcharge on the page
    body = client.get("/reliability").get_data(as_text=True)
    assert "Q8" in body


def test_manual_bad_price_no_500(rel_env, client):
    tok = _csrf(client)
    r = client.post("/reliability", data={
        "_csrf": tok, "__act": "manual", "supplier": "Q8", "country": "LV",
        "city": "Riga", "date": "2026-05-01", "net_price": "not-a-number"})
    assert r.status_code == 200            # rendered, not a crash
    assert b"Could not add advertised prices" in r.data
    assert not rel_env.list_advertised_prices()   # nothing stored


def test_csv_upload_loads_rows(rel_env, client):
    tok = _csrf(client)
    csv_text = ("supplier,country,city,date,net_price\n"
                "Q8,LV,Riga,2026-05-01,1.40\n"
                "BP,LV,Riga,2026-05-01,1.42\n")
    data = {"_csrf": tok, "__act": "upload", "country": "LV", "product_group": "Diesel",
            "file": (io.BytesIO(csv_text.encode("utf-8")), "adverts.csv")}
    r = client.post("/reliability", data=data,
                    content_type="multipart/form-data")
    assert r.status_code == 200
    assert b"Loaded 2 advertised price(s)" in r.data
    assert len(rel_env.list_advertised_prices()) == 2


def test_xlsx_upload_inherits_form_supplier(rel_env, client):
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    ws.append(["country", "city", "date", "net_price"])   # no supplier column
    ws.append(["LV", "Riga", "2026-05-01", 1.40])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    tok = _csrf(client)
    data = {"_csrf": tok, "__act": "upload", "supplier": "Q8", "country": "LV",
            "file": (buf, "adverts.xlsx")}
    r = client.post("/reliability", data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    assert b"Loaded 1 advertised price(s)" in r.data
    stored = rel_env.list_advertised_prices()
    assert stored and stored[0]["supplier"] == "Q8"   # inherited from the form field


def test_anonymous_redirects_to_login():
    import app as A
    c = A.app.test_client()           # no login
    r = c.get("/reliability")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
