"""
FULL-TEXT SEARCH (search.py, A2): an FTS5 index over the document/invoice corpus,
built by scanning the ENGINE-owned product DBs READ-ONLY into the app-owned search.db.

These tests stand up a tiny temp product corpus (a fuel_history.db with
invoice_documents + transactions and a suppliers.db with the supplier master +
supplier_invoices + VAT registrations), repoint dataproduct's read-only windows and
search.DB at the temp files, then exercise:
  - rebuild() then search() finds a doc by supplier name, by invoice ref, by product term,
    and by VAT number;
  - ranking returns the best match first (a query that is specific to one row puts it on top);
  - a query with punctuation / stray quotes doesn't raise and returns sensibly;
  - empty / whitespace / garbage-punctuation queries return [] without error;
  - rebuild() is idempotent (run twice -> identical counts);
  - the product DBs are opened READ-ONLY (a write through the same window raises);
  - the /search web page renders results (Flask test client, admin session) and ESCAPES a
    planted XSS term in the data.
"""
import os
import sqlite3
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import dataproduct  # noqa: E402
import db  # noqa: E402
import search  # noqa: E402

# The read-only `mode=ro` URI window is a SQLite mechanism (Postgres uses a read-only
# role — see dataproduct._role()); these tests assert that boundary holds.
pytestmark = pytest.mark.skipif(db.ENGINE != "sqlite",
                                reason="read-only URI handle is a SQLite mechanism")


def _build_products(tmp_path, xss=False):
    """Create a temp fuel_history.db + suppliers.db with a small corpus. With xss=True one
    supplier legal name carries a <script> payload (to prove the page escapes it)."""
    fh = str(tmp_path / "fuel_history.db")
    su = str(tmp_path / "suppliers.db")

    fc = sqlite3.connect(fh)
    fc.execute("CREATE TABLE invoice_documents (id INTEGER PRIMARY KEY, entity TEXT, "
               "supplier TEXT, invoice_ref TEXT, filename TEXT, kind TEXT, uploaded_at TEXT)")
    fc.execute("INSERT INTO invoice_documents VALUES "
               "(1,'Jupiter Plus AS','BP','INV-7788','bp_diesel.pdf','original_pdf','2026-05-01')")
    fc.execute("INSERT INTO invoice_documents VALUES "
               "(2,'Adverza Germany SIA','SHELL','ST-0001','shell_scan.pdf','scan','2026-05-02')")
    fc.execute("CREATE TABLE transactions (supplier TEXT, product TEXT, product_group TEXT)")
    fc.executemany("INSERT INTO transactions VALUES (?,?,?)",
                   [("BP", "AdBlue", "Other"), ("BP", "Diesel", "Diesel"),
                    ("SHELL", "RoadToll", "Toll")])
    fc.commit()
    fc.close()

    legal_bp = "British Petroleum Baltics"
    legal_shell = ('Shell <script>alert(1)</script> Eesti' if xss
                   else "Shell Eesti")
    sc = sqlite3.connect(su)
    sc.execute("CREATE TABLE suppliers (code TEXT, legal_name TEXT)")
    sc.executemany("INSERT INTO suppliers VALUES (?,?)",
                   [("BP", legal_bp), ("SHELL", legal_shell)])
    sc.execute("CREATE TABLE supplier_vat_registrations (supplier TEXT, country TEXT, "
               "vat_number TEXT, source TEXT)")
    sc.executemany("INSERT INTO supplier_vat_registrations VALUES (?,?,?,?)",
                   [("BP", "LT", "LT123456789", "x"), ("SHELL", "EE", "EE987654321", "x")])
    sc.execute("CREATE TABLE supplier_invoices (supplier TEXT, country TEXT, invoice_no TEXT, "
               "invoice_date TEXT, period TEXT, currency TEXT, gross_total REAL, notes TEXT)")
    sc.executemany("INSERT INTO supplier_invoices VALUES (?,?,?,?,?,?,?,?)",
                   [("BP", "LT", "INV-7788", "2026-04-30", "2026-04", "EUR", 1234.56,
                     "monthly diesel statement"),
                    ("SHELL", "EE", "ST-0001", "2026-04-29", "2026-04", "EUR", 555.00,
                     "road toll charges")])
    sc.commit()
    sc.close()
    return fh, su


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    """Repoint dataproduct's read-only windows + search.DB at a temp corpus, build it."""
    fh, su = _build_products(tmp_path)
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)
    monkeypatch.setitem(dataproduct._PATHS, "suppliers", su)
    monkeypatch.setattr(search, "DB", str(tmp_path / "search.db"), raising=True)
    return {"fh": fh, "su": su}


def _titles(rows):
    return [r["title"] for r in rows]


def test_rebuild_then_find_by_supplier_ref_product_and_vat(corpus):
    res = search.rebuild()
    assert res["rows"] == 4   # 2 documents + 2 supplier invoices

    # by supplier (legal) name
    assert any("BP" in t for t in _titles(search.search("Petroleum")))
    # by invoice ref
    assert any("INV-7788" in t for t in _titles(search.search("INV-7788")))
    # by product term (enriched from transactions)
    assert any("BP" in t for t in _titles(search.search("AdBlue")))
    assert any("SHELL" in t for t in _titles(search.search("RoadToll")))
    # by VAT number
    assert any("BP" in t for t in _titles(search.search("LT123456789")))


def test_ranking_best_match_first(corpus):
    search.rebuild()
    # "Petroleum" appears only for BP -> a BP row must rank first.
    rows = search.search("Petroleum")
    assert rows
    assert "BP" in rows[0]["title"]
    # bm25 score is monotonic (best/lowest first)
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores)


def test_punctuation_and_quotes_do_not_raise(corpus):
    search.rebuild()
    # Stray quotes / FTS operator punctuation must be neutralised, not error.
    for q in ['"INV-7788', 'BP AND OR NEAR()', 'diesel*', 'a:b^c', 'shell"" "']:
        rows = search.search(q)
        assert isinstance(rows, list)   # never raises
    # a real token inside the noise still finds its row
    assert any("INV-7788" in t for t in _titles(search.search('"INV-7788"')))


def test_empty_and_garbage_queries_return_empty(corpus):
    search.rebuild()
    for q in ["", "   ", "!!!", "()*", None]:
        assert search.search(q) == []


def test_rebuild_is_idempotent(corpus):
    a = search.rebuild()
    b = search.rebuild()
    assert a["rows"] == b["rows"] == 4
    # the result set is identical after a second rebuild (no duplication)
    r1 = search.search("Petroleum")
    search.rebuild()
    r2 = search.search("Petroleum")
    assert [x["rowkey"] for x in r1] == [x["rowkey"] for x in r2]


def test_search_missing_index_returns_empty(tmp_path, monkeypatch):
    """No rebuild yet (no search.db / no corpus table) -> search() returns [] not raises."""
    monkeypatch.setattr(search, "DB", str(tmp_path / "never_built.db"), raising=True)
    assert search.search("anything") == []


def test_product_dbs_opened_read_only(corpus):
    """rebuild() must read the product DBs through the READ-ONLY window — a write through
    the same handle raises OperationalError (the data-product boundary)."""
    con = dataproduct.connect("fuel_history")
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO invoice_documents (id) VALUES (999)")
            con.commit()
    finally:
        con.close()
    con = dataproduct.connect("suppliers")
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("UPDATE suppliers SET legal_name='x'")
            con.commit()
    finally:
        con.close()


def test_search_page_renders_and_escapes_xss(tmp_path, monkeypatch, client):
    """The /search page renders ranked results (admin session) and ESCAPES a planted XSS
    term that lives in the indexed data — the raw <script> must not appear in the page."""
    fh, su = _build_products(tmp_path, xss=True)
    monkeypatch.setitem(dataproduct._PATHS, "fuel_history", fh)
    monkeypatch.setitem(dataproduct._PATHS, "suppliers", su)
    monkeypatch.setattr(search, "DB", str(tmp_path / "search.db"), raising=True)
    # also repoint the app's reference to the module (it imports `search as _search`)
    import app as A
    monkeypatch.setattr(A._search, "DB", str(tmp_path / "search.db"), raising=True)
    search.rebuild()

    # empty page renders
    r0 = client.get("/search")
    assert r0.status_code == 200

    # a query that matches the XSS-carrying SHELL row
    r = client.get("/search?q=Shell")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "ST-0001" in html or "SHELL" in html        # the result is shown
    assert "<script>alert(1)</script>" not in html      # the payload is escaped
    assert "&lt;script&gt;" in html                      # escaped form present
