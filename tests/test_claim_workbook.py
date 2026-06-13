"""First real coverage of `vat_refund.invoice_lines` and the claim-pack workbook.

These tests drive the line-structure + workbook RENDER path against real in-memory
analytics / supplier / customer DBs. They assert the SAFE Phase-1 behaviour:
  * Order A: a pack with any synthetic line (INPUT / ALL: / UNMATCHED) is refused at
    the workbook — a single bold-red BLOCKED banner replaces the data rows; no cell
    leaks an `ALL:`/`INPUT:` ref.
  * Order B: `invoice_lines` emits exactly one row per (resolved invoice, single
    product code), never an `ALL:` aggregate; unresolved transactions become one
    explicit `UNMATCHED` row that `_synthetic()` flags as a hard block.

No money math or invoice-lock behaviour is exercised/changed here.
"""
import importlib

import openpyxl
import pytest


def _modules(tmp_path, monkeypatch):
    import customer_master, supplier_master, vat_refund
    importlib.reload(customer_master); importlib.reload(supplier_master)
    importlib.reload(vat_refund)
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "cdocs"))
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "s.db"))
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "a.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    return customer_master, supplier_master, vat_refund


def _analytics(vr):
    # transactions is engine-owned; analytics_connect() is now a READ-ONLY handle,
    # so seed the fixture data through a direct writable connection to the (tmp-path,
    # monkeypatched) ANALYTICS_DB. The claim code under test still reads via
    # analytics_connect().
    import sqlite3
    ac = sqlite3.connect(vr.ANALYTICS_DB)
    ac.row_factory = sqlite3.Row
    ac.execute("""CREATE TABLE IF NOT EXISTS transactions (
        period TEXT, entity TEXT, supplier TEXT, country TEXT, vehicle TEXT,
        date TEXT, time TEXT, station TEXT, product TEXT, product_group TEXT,
        qty REAL, currency TEXT, net_local REAL, vat_local REAL, gross_local REAL,
        net_eur REAL, vat_eur REAL, net_eur_eff REAL, note TEXT)""")
    return ac


def _txn(ac, *, period, entity, supplier, country, product_group, note,
         net=100.0, vat=21.0, currency="EUR"):
    ac.execute("""INSERT INTO transactions
        (period, entity, supplier, country, product_group, qty, currency,
         net_local, vat_local, net_eur, vat_eur, net_eur_eff, note)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (period, entity, supplier, country, product_group, 50.0, currency,
         net, vat, net, vat, net, note))


def _supplier(sm, *, code="BP", country="Belgium", invoices=()):
    con = sm.connect()
    con.execute("INSERT INTO suppliers (code, legal_name) VALUES (?,?)",
                (code, "B2Mobility GmbH"))
    # a real VAT registration so the line is NOT INPUT-by-vat-id (isolate the ref check)
    con.execute("""INSERT INTO supplier_vat_registrations (supplier, country, vat_number, source)
                   VALUES (?,?,?,?)""", (code, country, "BE0123456789", "registry"))
    for no, date in invoices:
        con.execute("""INSERT INTO supplier_invoices (supplier, country, invoice_no, invoice_date)
                       VALUES (?,?,?,?)""", (code, country, no, date))
    con.commit(); con.close()


def _customer(cm, *, code="ACME", name="Acme SIA"):
    # Fully populated so no INCIDENTAL "INPUT" placeholder appears in the applicant
    # header — keeping the INPUT/ALL: leak assertions about CLAIM LINES specifically.
    cm.add_customer(code, name, "LV")
    con = cm.connect()
    con.execute("""UPDATE customers SET reg_number='LV123', vat_number='LV456',
                   legal_address='Riga 1', home_portal='https://lv.vat.portal' WHERE code=?""",
                (code,))
    con.execute("""INSERT INTO customer_bank_accounts (customer, iban, bank, currency, purpose)
                   VALUES (?,?,?,?,?)""", (code, "LV99TESTIBAN", "TestBank", "EUR", "refund payout"))
    con.commit(); con.close()


# ----------------------------------------------------------------- Order A
def test_workbook_blocks_synthetic_all_aggregate(tmp_path, monkeypatch):
    """A transaction whose note matches NO registered invoice for a supplier with >=2
    registered invoices forces the (legacy) ALL: branch -> the workbook must refuse to
    emit data rows: a BLOCKED banner replaces them and no cell leaks ALL:/INPUT:."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    _customer(cm)
    _supplier(sm, invoices=[("INV-A", "2026-04-01"), ("INV-B", "2026-04-02")])
    ac = _analytics(vr)
    _txn(ac, period="2026-04", entity="Acme SIA", supplier="BP", country="Belgium",
         product_group="Diesel", note="no-such-invoice-ref")
    ac.commit(); ac.close()

    con = vr.connect()
    path, _matrix = vr.build_workbook(con, 2026)
    con.close()

    wb = openpyxl.load_workbook(path)
    # the claim sheet (not the Overview tab)
    claim = [ws for ws in wb.worksheets if ws.title != "Overview"]
    assert claim, "expected at least one per-claim sheet"
    ws = claim[0]
    cells = [c.value for row in ws.iter_rows() for c in row if c.value is not None]
    banner = [v for v in cells if isinstance(v, str)
              and v.startswith("BLOCKED - ") and "resolve before filing" in v]
    assert banner, f"expected a BLOCKED banner, got: {cells}"
    # nothing synthetic leaks into a submittable pack
    for v in cells:
        s = str(v)
        assert not s.startswith("ALL:"), f"ALL: aggregate leaked: {v}"
        assert "INPUT:" not in s, f"INPUT: placeholder leaked: {v}"
    # no SUM TOTAL formula was emitted (the total is a hard zero instead)
    assert not any(isinstance(v, str) and v.startswith("=SUM(") for v in cells)


# ----------------------------------------------------------------- Order B
def test_invoice_lines_resolves_to_single_matched_invoice(tmp_path, monkeypatch):
    """Supplier with 2 registered invoices + a txn whose note matches ONE of them ->
    exactly one row tied to that specific invoice, never an ALL: aggregate."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    _customer(cm)
    _supplier(sm, invoices=[("INV-A", "2026-04-01"), ("INV-B", "2026-04-02")])
    ac = _analytics(vr)
    _txn(ac, period="2026-04", entity="Acme SIA", supplier="BP", country="Belgium",
         product_group="Diesel", note="INV-A fuel run")
    ac.commit(); ac.close()

    con = vr.connect()
    lines = vr.invoice_lines(con, "Acme SIA", "Belgium", "2026-Q2")
    con.close()
    assert len(lines) == 1
    assert lines[0]["invoice"] == "INV-A"
    assert not any(str(L["invoice"]).startswith("ALL:") for L in lines)
    assert not vr._synthetic(lines[0]["invoice"], lines[0].get("vat_id"))


def test_invoice_lines_one_row_per_product_code(tmp_path, monkeypatch):
    """Two product codes on the SAME matched invoice -> two rows, one per code."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    _customer(cm)
    _supplier(sm, invoices=[("INV-A", "2026-04-01"), ("INV-B", "2026-04-02")])
    ac = _analytics(vr)
    _txn(ac, period="2026-04", entity="Acme SIA", supplier="BP", country="Belgium",
         product_group="Diesel", note="INV-A diesel")
    _txn(ac, period="2026-04", entity="Acme SIA", supplier="BP", country="Belgium",
         product_group="AdBlue", note="INV-A adblue")
    ac.commit(); ac.close()

    con = vr.connect()
    lines = vr.invoice_lines(con, "Acme SIA", "Belgium", "2026-Q2")
    con.close()
    assert all(L["invoice"] == "INV-A" for L in lines)
    assert {L["product"] for L in lines} == {"Diesel", "AdBlue"}
    assert len(lines) == 2


def test_invoice_lines_unmatched_marker_is_synthetic(tmp_path, monkeypatch):
    """A txn matching no invoice (supplier has 2 registered) -> a single UNMATCHED row
    that carries the VAT and is flagged synthetic (never an ALL: aggregate)."""
    cm, sm, vr = _modules(tmp_path, monkeypatch)
    _customer(cm)
    _supplier(sm, invoices=[("INV-A", "2026-04-01"), ("INV-B", "2026-04-02")])
    ac = _analytics(vr)
    _txn(ac, period="2026-04", entity="Acme SIA", supplier="BP", country="Belgium",
         product_group="Diesel", note="no-match-here", net=80.0, vat=16.8)
    ac.commit(); ac.close()

    con = vr.connect()
    lines = vr.invoice_lines(con, "Acme SIA", "Belgium", "2026-Q2")
    con.close()
    assert len(lines) == 1
    row = lines[0]
    assert row["invoice"] == "UNMATCHED"
    assert not str(row["invoice"]).startswith("ALL:")
    assert float(row["vat_eur"]) == pytest.approx(16.8)
    assert vr._synthetic(row["invoice"])
