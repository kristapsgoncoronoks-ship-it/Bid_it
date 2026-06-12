"""Competitor FX history & control: store each supplier's implied rate vs ECB as a
historic pattern, and detect whether the markup is increasing/decreasing vs the market."""
import importlib

import pytest


@pytest.fixture()
def sfx(tmp_path, monkeypatch):
    import supplier_fx, ecb_rates
    importlib.reload(supplier_fx)
    monkeypatch.setattr(supplier_fx, "DB", str(tmp_path / "fh.db"))
    supplier_fx._READY.clear()
    con = supplier_fx.connect()
    con.execute("""CREATE TABLE transactions (supplier, currency, period, date,
                   net_local, net_eur)""")
    con.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?)", [
        ("BP", "PLN", "2026-04", "2026-04-30", 4400.0, 1000.0),   # implied 4.40
        ("BP", "PLN", "2026-05", "2026-05-31", 4500.0, 1000.0),   # implied 4.50 (markup grew)
    ])
    con.commit(); con.close()
    monkeypatch.setattr(ecb_rates, "rate_for", lambda ccy, on=None: (4.30, on))  # market fixed
    return supplier_fx


def test_snapshot_stores_historic_pattern(sfx):
    assert sfx.snapshot() == 2
    h = sfx.history(supplier="BP", currency="PLN")
    assert len(h) == 2
    by = {r["period"]: round(r["deviation_pct"], 2) for r in h}
    assert by["2026-04"] == 2.33 and by["2026-05"] == 4.65   # markup vs ECB 4.30


def test_trend_flags_increasing_markup_vs_market(sfx):
    sfx.snapshot()
    t = sfx.trend()[0]
    assert t["supplier"] == "BP" and t["period"] == "2026-05"
    assert t["direction"] == "increasing" and t["increasing"] is True
    assert round(t["delta"], 2) == 2.33                      # +4.65 - +2.33 pp


def test_trend_decreasing(sfx, monkeypatch):
    # flip the data so the markup shrinks period over period
    con = sfx.connect()
    con.execute("UPDATE transactions SET net_local=4500.0 WHERE period='2026-04'")  # 4.50
    con.execute("UPDATE transactions SET net_local=4400.0 WHERE period='2026-05'")  # 4.40
    con.commit(); con.close()
    sfx.snapshot()
    t = next(x for x in sfx.trend() if x["period"] == "2026-05")
    assert t["direction"] == "decreasing" and t["increasing"] is False


def test_snapshot_idempotent(sfx):
    sfx.snapshot()
    assert sfx.snapshot() == 2
    assert len(sfx.history()) == 2                           # no duplicate rows
