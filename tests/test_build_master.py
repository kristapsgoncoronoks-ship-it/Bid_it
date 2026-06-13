"""
Master workbook (build_master.build) regression tests for the FX-analysis sheet,
the explicit per-line FX rate on Transactions, the Python-computed cross-tab pivots,
and the Transactions Excel Table.

These run against the demo data: consolidate.py is executed once to (re)write the
period-stamped pickle, then build() is called and the resulting .xlsx is opened with
openpyxl and asserted. The generated workbook is cleaned up; we never assert against a
populated ecb_rates.db (it is gitignored / network-seeded) — for the ECB-dependent
markup we seed a throwaway ecb_rates DB so the deviation column is deterministic.
"""
import collections
import os
import subprocess
import sys

import openpyxl
import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import money  # noqa: E402

PERIOD = "2026-05"


@pytest.fixture(scope="module")
def workbook(tmp_path_factory):
    """Consolidate the demo period, point ecb_rates at a throwaway DB seeded with a
    known PLN rate, build the master workbook, and yield (workbook, rows). The output
    .xlsx is removed on teardown; the real ecb_rates.DB is restored."""
    proc = subprocess.run([sys.executable, os.path.join(WORKDIR, "consolidate.py")],
                          cwd=WORKDIR, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    import consolidate
    import ecb_rates
    import build_master

    rows = consolidate.load_rows(PERIOD)

    # Deterministic ECB cache: a single seeded PLN rate so the FX-analysis deviation /
    # markup for the BP/PLN row is computed regardless of the live (gitignored) cache.
    seed_db = tmp_path_factory.mktemp("ecb") / "ecb_rates.db"
    orig_db = ecb_rates.DB
    ecb_rates.DB = str(seed_db)
    try:
        ecb_rates.store([("2026-05-31", "PLN", 4.31)], source="test seed")
        out_path = build_master.build(PERIOD)
        wb = openpyxl.load_workbook(out_path)
    finally:
        ecb_rates.DB = orig_db

    yield wb, rows
    try:
        os.remove(out_path)
    except OSError:
        pass


def test_expected_sheets_present(workbook):
    wb, _ = workbook
    for name in ("Transactions", "FX analysis", "Pivot Supplier x Country",
                 "Pivot Entity x Country", "Pivot Product x Country"):
        assert name in wb.sheetnames, f"missing sheet {name}: {wb.sheetnames}"


def test_transactions_fx_rate_column_consistent(workbook):
    """The Transactions sheet carries an explicit FX-rate column (S), populated, and
    net_local / rate == net_eur for non-EUR lines; EUR lines carry rate 1.0."""
    wb, _ = workbook
    ws = wb["Transactions"]
    assert ws.cell(row=1, column=19).value == "FX rate (local/EUR)"
    checked = 0
    for rr in range(2, ws.max_row + 1):
        ccy = ws.cell(row=rr, column=11).value
        net_local = ws.cell(row=rr, column=12).value
        net_eur = ws.cell(row=rr, column=15).value
        fx = ws.cell(row=rr, column=19).value
        if not ccy or ccy == "EUR":
            assert fx == 1.0, f"EUR line {rr} fx={fx!r}"
            continue
        if net_eur and fx:
            # net_local / rate ~= net_eur (rate = foreign units per 1 EUR)
            assert abs(net_local / fx - net_eur) < 0.01, (rr, net_local, fx, net_eur)
            checked += 1
    assert checked > 0, "expected at least one non-EUR line to verify"


def test_fx_analysis_known_supplier_row(workbook):
    """FX-analysis sheet exists with a deviation/markup column and a known supplier row
    (BP/PLN): implied rate = net_local/net_eur, and with the seeded ECB rate the
    deviation % and EUR markup are populated and self-consistent."""
    wb, _ = workbook
    ws = wb["FX analysis"]
    hdr = [c.value for c in ws[4]]
    assert "Deviation %" in hdr and "EUR gain/loss (markup)" in hdr, hdr
    ix = {h: i for i, h in enumerate(hdr)}

    bp = None
    for rr in range(5, ws.max_row + 1):
        if ws.cell(row=rr, column=1).value == "BP" and ws.cell(row=rr, column=2).value == "PLN":
            bp = [c.value for c in ws[rr]]
            break
    assert bp is not None, "BP/PLN row not found in FX analysis"

    net_local = bp[ix["Net local"]]
    net_eur = bp[ix["Net EUR"]]
    implied = bp[ix["Implied rate"]]
    ecb = bp[ix["ECB rate"]]
    dev = bp[ix["Deviation %"]]
    markup = bp[ix["EUR gain/loss (markup)"]]

    assert abs(implied - net_local / net_eur) < 1e-4, (implied, net_local, net_eur)
    assert abs(ecb - 4.31) < 1e-6, ecb
    # deviation = (implied - ecb) / ecb * 100
    assert abs(dev - (implied - ecb) / ecb * 100) < 1e-3, (dev, implied, ecb)
    # eur markup = net_eur - net_local / ecb
    assert abs(markup - money.f2(net_eur - net_local / ecb)) < 0.01, (markup,)


def test_pivot_supplier_country_cell_matches_raw(workbook):
    """A known cell in Pivot Supplier x Country (BP, Poland) matches the litres and
    Net EUR summed from the raw rows."""
    wb, rows = workbook
    raw_litres = collections.defaultdict(float)
    raw_net = collections.defaultdict(float)
    for r_ in rows:
        raw_litres[(r_[1], r_[2])] += r_[9] or 0.0
        raw_net[(r_[1], r_[2])] += r_[14] or 0.0

    ws = wb["Pivot Supplier x Country"]
    hdr = [c.value for c in ws[5]]
    assert "Poland" in hdr, hdr
    pol = hdr.index("Poland") + 1

    # litres block: first BP row under header row 5
    bp_litres = None
    for rr in range(6, ws.max_row + 1):
        if ws.cell(row=rr, column=1).value == "BP":
            bp_litres = ws.cell(row=rr, column=pol).value
            break
    assert bp_litres == round(raw_litres[("BP", "Poland")], 0), bp_litres

    # Net EUR block: BP row under the "Net EUR" metric header
    bp_net = None
    for rr in range(1, ws.max_row + 1):
        if ws.cell(row=rr, column=1).value == "Net EUR":
            for k in range(rr + 2, ws.max_row + 1):
                if ws.cell(row=k, column=1).value == "BP":
                    bp_net = ws.cell(row=k, column=pol).value
                    break
            break
    assert bp_net == money.f2(raw_net[("BP", "Poland")]), bp_net


def test_transactions_registered_as_excel_table(workbook):
    """The Transactions data range is registered as a proper Excel Table so the user
    can drop their own native PivotTables / filters on clean tabular data."""
    wb, _ = workbook
    ws = wb["Transactions"]
    assert "TransactionsTbl" in ws.tables, list(ws.tables)
    ref = ws.tables["TransactionsTbl"].ref
    assert ref.startswith("A1:S"), ref
