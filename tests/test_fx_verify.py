"""Independent per-invoice ECB verification (owner-directed compliance).

supplier_fx.verify_invoices_fx() compares each invoice/fuelling-day's APPLIED rate
(net_local/net_eur) against the OFFICIAL ECB reference frozen on that line at
consolidation (fx_ecb_rate/fx_ecb_date/fx_source written by history.ecb_reference),
flags deviations >= the established 2% threshold, and reports lines with NO ECB
reference as 'no reference' rather than a false pass.
"""
import importlib

import pytest


@pytest.fixture()
def sfx(tmp_path, monkeypatch):
    import supplier_fx
    importlib.reload(supplier_fx)
    monkeypatch.setattr(supplier_fx, "DB", str(tmp_path / "fh.db"))
    supplier_fx._READY.clear()
    con = supplier_fx.connect()
    # the full transactions shape the helper reads (incl. the frozen ECB reference cols)
    con.execute("""CREATE TABLE transactions (supplier, currency, period, date,
                   net_local, net_eur, fx_ecb_rate, fx_ecb_date, fx_source)""")
    con.executemany(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?)", [
            # applied 4.40 vs ECB 4.30 -> +2.33% -> FLAGGED (>= 2%)
            ("BP", "PLN", "2026-04", "2026-04-30", 4400.0, 1000.0, 4.30, "2026-04-30", "ecb"),
            # applied 4.34 vs ECB 4.30 -> +0.93% -> within 2%, NOT flagged
            ("Shell", "PLN", "2026-04", "2026-04-29", 4340.0, 1000.0, 4.30, "2026-04-29", "ecb"),
            # no ECB coverage stored -> reported as 'no reference', never a pass
            ("Circle", "SEK", "2026-04", "2026-04-28", 11500.0, 1000.0, None, None, "none"),
            # EUR line -> trivially OK, excluded from the verification table
            ("TFC", "EUR", "2026-04", "2026-04-30", 450.0, 450.0, 1.0, "2026-04-30", "eur"),
        ])
    con.commit(); con.close()
    return supplier_fx


def _by_supplier(rows):
    return {r["supplier"]: r for r in rows}


def test_flags_line_deviating_2pct(sfx):
    rows = _by_supplier(sfx.verify_invoices_fx(period="2026-04"))
    bp = rows["BP"]
    assert bp["fx_source"] == "ecb" and bp["no_ref"] is False
    assert round(bp["deviation_pct"], 2) == 2.33
    assert bp["flagged"] is True
    assert abs(bp["applied_rate"] - 4.40) < 1e-9


def test_within_tolerance_not_flagged(sfx):
    rows = _by_supplier(sfx.verify_invoices_fx(period="2026-04"))
    sh = rows["Shell"]
    assert sh["no_ref"] is False
    assert round(sh["deviation_pct"], 2) == 0.93
    assert sh["flagged"] is False


def test_no_coverage_reported_not_pass(sfx):
    rows = _by_supplier(sfx.verify_invoices_fx(period="2026-04"))
    ci = rows["Circle"]
    assert ci["no_ref"] is True             # no official reference exists
    assert ci["flagged"] is False           # NOT a pass
    assert ci["ecb_rate"] is None and ci["deviation_pct"] is None


def test_eur_lines_excluded(sfx):
    rows = _by_supplier(sfx.verify_invoices_fx(period="2026-04"))
    assert "TFC" not in rows                 # EUR (rate 1.0) excluded


def test_sorted_worst_deviation_first(sfx):
    rows = sfx.verify_invoices_fx(period="2026-04")
    # rows WITH a deviation come before the no-reference one; BP (2.33) before Shell (0.93)
    devs = [r["deviation_pct"] for r in rows if r["deviation_pct"] is not None]
    assert devs == sorted(devs, key=lambda d: -abs(d))
    assert rows[0]["supplier"] == "BP"


def test_no_ecb_columns_returns_empty(tmp_path, monkeypatch):
    """A legacy DB without the fx_ecb_* columns yields [] rather than erroring."""
    import supplier_fx
    importlib.reload(supplier_fx)
    monkeypatch.setattr(supplier_fx, "DB", str(tmp_path / "legacy.db"))
    supplier_fx._READY.clear()
    con = supplier_fx.connect()
    con.execute("""CREATE TABLE transactions (supplier, currency, period, date,
                   net_local, net_eur)""")
    con.execute("INSERT INTO transactions VALUES ('BP','PLN','2026-04','2026-04-30',4400.0,1000.0)")
    con.commit(); con.close()
    assert supplier_fx.verify_invoices_fx(period="2026-04") == []
