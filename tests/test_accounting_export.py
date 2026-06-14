"""Accounting / ERP ledger export (CSV) — the decision-free first cut of the
SAF-T/ERP-export capability.

Covers:
- queries.q_ledger: one row per transaction; gross_eur == net_eur + vat_eur (quantized)
  and gross_local == net_local + vat_local on every row; vat_rate_pct correct on a
  hand-checkable row and 0.0 on a net_eur==0 row; the entity filter restricting rows;
  the never-raise contract (a bogus period / a connection with no table returns []).
- Reconciliation: the per-entity sum of q_ledger net_eur/vat_eur ties to
  queries.q_expense(...).by_entity. Exact equality is NOT guaranteed — q_ledger quantizes
  per LINE (money.f2 each row) whereas q_expense sums full-precision then quantizes ONCE
  per entity, so the two can differ by sub-cent rounding per line. We assert the gap stays
  within one cent per line (|diff| <= 0.01 * n_rows_for_entity).
- reports.accounting_ledger_csv: builds; parses back with csv.reader (header + N rows ==
  len(q_ledger)); a numeric column round-trips; filename carries the period; raises
  ValueError when there is no data/period.
- Web: /export/accounting returns 200 text/csv with a CSV body for an authenticated user;
  the endpoint is gated under "exports" / the analytics module.

BASIS: NET EUR, final (rebates applied); VAT shown separately; gross = net + VAT.
Pure READ-ONLY over the engine-owned transactions.
"""
import csv
import io
import sqlite3

import money
import queries
import reports


# Full transactions shape q_ledger SELECTs from (the columns it reads).
_COLS = ("date", "period", "entity", "supplier", "country", "vehicle", "station",
         "product", "product_group", "qty", "currency", "net_local", "vat_local",
         "net_eur", "vat_eur", "net_eur_eff", "note")


def _seed(rows):
    """rows: dicts keyed by any subset of _COLS (missing keys default to None/'')."""
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    con.execute(f"CREATE TABLE transactions ({', '.join(c + ' TEXT' if c in ('date','period','entity','supplier','country','vehicle','station','product','product_group','currency','note') else c + ' REAL' for c in _COLS)})")
    con.executemany(
        f"INSERT INTO transactions ({','.join(_COLS)}) VALUES ({','.join('?' for _ in _COLS)})",
        [tuple(r.get(c) for c in _COLS) for r in rows])
    con.commit()
    return con


def _row(**kw):
    base = {"date": "2026-05-01", "period": "2026-05", "entity": "A", "supplier": "S",
            "country": "Latvia", "vehicle": "V1", "station": "ST", "product": "Diesel",
            "product_group": "Diesel", "qty": 100.0, "currency": "EUR",
            "net_local": 130.0, "vat_local": 27.3, "net_eur": 130.0, "vat_eur": 27.3,
            "net_eur_eff": 128.0, "note": ""}
    base.update(kw)
    return base


# ---------------------------------------------------------------- q_ledger

def test_ledger_gross_is_sum_of_parts_every_row():
    con = _seed([
        _row(net_eur=130.0, vat_eur=27.3, net_local=130.0, vat_local=27.3),
        _row(entity="B", net_eur=18.05, vat_eur=3.79, net_local=20.0, vat_local=4.2,
             currency="PLN", country="Poland"),
    ])
    rows = queries.q_ledger(con, "2026-05")
    con.close()
    assert len(rows) == 2
    for r in rows:
        assert r["gross_eur"] == money.f2(r["net_eur"] + r["vat_eur"])
        assert r["gross_local"] == r["net_local"] + r["vat_local"]


def test_ledger_vat_rate_hand_checkable():
    # net 100 EUR, vat 21 EUR -> 21.0%
    con = _seed([_row(net_eur=100.0, vat_eur=21.0)])
    rows = queries.q_ledger(con, "2026-05")
    con.close()
    assert rows[0]["vat_rate_pct"] == 21.0


def test_ledger_vat_rate_zero_on_zero_net():
    # A zero-net line (e.g. a fully-rebated / corrective row) must not divide-by-zero.
    con = _seed([_row(net_eur=0.0, vat_eur=0.0)])
    rows = queries.q_ledger(con, "2026-05")
    con.close()
    assert rows[0]["vat_rate_pct"] == 0.0


def test_ledger_entity_filter_restricts_rows():
    con = _seed([
        _row(entity="A"),
        _row(entity="A"),
        _row(entity="B"),
    ])
    rows = queries.q_ledger(con, "2026-05", entity="A")
    con.close()
    assert len(rows) == 2
    assert {r["entity"] for r in rows} == {"A"}


def test_ledger_sorted_by_date_entity_supplier():
    con = _seed([
        _row(date="2026-05-03", entity="B", supplier="Z"),
        _row(date="2026-05-01", entity="A", supplier="Y"),
        _row(date="2026-05-01", entity="A", supplier="X"),
    ])
    rows = queries.q_ledger(con, "2026-05")
    con.close()
    keys = [(r["date"], r["entity"], r["supplier"]) for r in rows]
    assert keys == sorted(keys)


def test_ledger_never_raises_on_bad_period():
    con = _seed([_row()])
    rows = queries.q_ledger(con, "1999-01")   # no such period
    con.close()
    assert rows == []


def test_ledger_never_raises_on_missing_table():
    con = sqlite3.connect(":memory:")          # no transactions table
    rows = queries.q_ledger(con, "2026-05")
    con.close()
    assert rows == []


# ---------------------------------------------- reconciliation with q_expense

def test_ledger_reconciles_with_q_expense_by_entity():
    """Per-entity sums of q_ledger net_eur/vat_eur tie to q_expense's by_entity.

    NOT exact: q_ledger quantizes money.f2 per LINE, q_expense sums full precision then
    quantizes ONCE per entity. The gap is bounded by one cent per line, so we assert
    |diff| <= 0.01 * n_rows_for_entity on each entity."""
    # Values chosen with sub-cent thirds so per-line vs aggregate rounding can diverge.
    con = _seed([
        _row(entity="A", net_eur=10.005, vat_eur=2.101),
        _row(entity="A", net_eur=10.005, vat_eur=2.101),
        _row(entity="A", net_eur=10.005, vat_eur=2.101),
        _row(entity="B", net_eur=33.337, vat_eur=7.001),
        _row(entity="B", net_eur=33.337, vat_eur=7.001),
    ])
    ledger = queries.q_ledger(con, "2026-05")
    exp = queries.q_expense(con, "2026-05")
    con.close()

    led_by_entity = {}
    for r in ledger:
        agg = led_by_entity.setdefault(r["entity"], {"net": 0.0, "vat": 0.0, "n": 0})
        agg["net"] += r["net_eur"]; agg["vat"] += r["vat_eur"]; agg["n"] += 1

    exp_by_entity = {e["entity"]: e for e in exp["by_entity"]}
    assert set(led_by_entity) == set(exp_by_entity)
    for ent, agg in led_by_entity.items():
        n = agg["n"]
        assert abs(agg["net"] - exp_by_entity[ent]["net_eur"]) <= 0.01 * n
        assert abs(agg["vat"] - exp_by_entity[ent]["vat_eur"]) <= 0.01 * n


# ------------------------------------------------- accounting_ledger_csv

def test_csv_builds_and_round_trips(tmp_path):
    # Use the real seeded product DB / latest period via the module's connect()/_periods().
    con = reports.connect()
    period = reports._periods(con)[0]
    ledger = queries.q_ledger(con, period)
    con.close()

    name, data = reports.accounting_ledger_csv(period)
    assert isinstance(data, bytes)
    assert name == f"Accounting_Ledger_{period}.csv"

    # utf-8-sig BOM so Excel opens it cleanly.
    assert data[:3] == b"\xef\xbb\xbf"

    text = data.decode("utf-8-sig")
    parsed = list(csv.reader(io.StringIO(text)))
    header, body = parsed[0], parsed[1:]
    assert header[0] == "Date" and header[-1] == "Note"
    assert "Net EUR" in header and "Gross EUR" in header
    assert len(body) == len(ledger)

    # A numeric column round-trips to the q_ledger value.
    net_idx = header.index("Net EUR")
    assert float(body[0][net_idx]) == ledger[0]["net_eur"]


def test_csv_default_period_is_latest():
    name, data = reports.accounting_ledger_csv()        # no period -> latest
    con = reports.connect()
    latest = reports._periods(con)[0]
    con.close()
    assert name == f"Accounting_Ledger_{latest}.csv"


def test_csv_entity_suffix_filesystem_safe():
    name, _ = reports.accounting_ledger_csv("2026-05", entity="Jupiter Plus AS")
    assert name.startswith("Accounting_Ledger_2026-05_")
    assert name.endswith(".csv")
    # no spaces / unsafe chars leaked into the filename
    assert " " not in name


def test_csv_raises_when_no_data():
    import unittest.mock as mock
    with mock.patch.object(reports, "_periods", return_value=[]):
        try:
            reports.accounting_ledger_csv()
            assert False, "expected ValueError"
        except ValueError as e:
            assert "nothing to export" in str(e)


# ---------------------------------------------------------------- web

def test_export_accounting_route(client):
    r = client.get("/export/accounting?period=2026-05")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    body = r.get_data()
    assert body[:3] == b"\xef\xbb\xbf"          # utf-8-sig BOM
    text = body.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == "Date"                 # header row present
    assert len(rows) >= 2                        # header + at least one data row


def test_export_accounting_is_gated():
    import app
    assert app.PERM_BY_ENDPOINT.get("export_accounting") == "exports"
    assert "export_accounting" in app.MODULES["analytics"][1]
    assert "export_accounting" not in app.ADMIN_ONLY


def test_expenses_page_has_ledger_button(client):
    r = client.get("/expenses?period=2026-05")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Download accounting ledger (CSV)" in html
    assert "/export/accounting?period=2026-05" in html
