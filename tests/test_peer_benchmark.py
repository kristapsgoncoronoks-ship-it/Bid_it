"""
PER-ENTITY vs PEER internal benchmark (M1) — pricing_intelligence.peer_benchmark.

The peer aggregate is the EQUAL-WEIGHT-PER-ENTITY MEDIAN of the OTHER entities'
effective NET EUR/L in the same (country, period-bucket), EXCLUDING the entity itself.
Where an entity pays ABOVE that median the gap x litres is "addressable" spend. A cell
with fewer than PEER_MIN_CONTRIBUTORS other entities is SUPPRESSED ("cohort too small")
so no single entity is singled out.

Seeds the engine-owned product DB directly (the app reads `transactions` read-only) and
asserts: per-entity median excludes self, gap/addressable_eur are correct, the headline
sums the POSITIVE gaps, a 1-other-entity cell is suppressed, and bucket_period groups an
off-period straggler into the period cell. A web assertion proves the /pricing peer card
renders 200 with escaped DB values and labels suppressed cells.
"""
import importlib
import sqlite3

import pytest


def _seed(tmp_path, monkeypatch, rows):
    """Reload pricing_intelligence pointed at a throwaway product DB and seed
    `transactions` (incl. the `entity` column peer_benchmark groups on)."""
    import pricing_intelligence
    importlib.reload(pricing_intelligence)
    fuel = str(tmp_path / "fuel_history.db")
    monkeypatch.setattr(pricing_intelligence, "DB", fuel)
    monkeypatch.setattr(pricing_intelligence, "BENCHMARK_DB", str(tmp_path / "benchmark.db"))
    prod = sqlite3.connect(fuel)
    prod.execute("""CREATE TABLE IF NOT EXISTS transactions (
        period TEXT, entity TEXT, country TEXT, supplier TEXT, station TEXT, date TEXT,
        product_group TEXT, qty REAL, net_eur_eff REAL)""")
    prod.executemany(
        "INSERT INTO transactions (period,entity,country,supplier,station,date,"
        "product_group,qty,net_eur_eff) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    prod.commit(); prod.close()
    return pricing_intelligence


@pytest.fixture()
def pi(tmp_path, monkeypatch):
    # Three entities in ONE (country, period) at different eff EUR/L:
    #   A = 1.50, B = 1.40, C = 1.30  (qty 1000 each so eff = net/qty)
    # Plus a separate (country, period) with only TWO entities => for each, exactly ONE
    # other entity => below PEER_MIN_CONTRIBUTORS (2) => SUPPRESSED.
    return _seed(tmp_path, monkeypatch, [
        ("2026-05", "A", "France", "BP", "Lille", "2026-05-10", "Diesel", 1000, 1500.0),  # 1.50
        ("2026-05", "B", "France", "BP", "Lille", "2026-05-11", "Diesel", 1000, 1400.0),  # 1.40
        ("2026-05", "C", "France", "BP", "Lille", "2026-05-12", "Diesel", 1000, 1300.0),  # 1.30
        # only-two-entities cell -> each has exactly 1 other -> suppressed
        ("2026-05", "A", "Spain", "Q8", "Madrid", "2026-05-10", "Diesel", 1000, 1200.0),
        ("2026-05", "B", "Spain", "Q8", "Madrid", "2026-05-10", "Diesel", 1000, 1250.0),
    ])


def _row(rows, entity, country):
    return next(r for r in rows if r["entity"] == entity and r["country"] == country)


def test_peer_median_excludes_self(pi):
    rows, _ = pi.peer_benchmark("2026-05", "month", "Diesel")
    # A's peers are {B=1.40, C=1.30} -> median 1.35 (mean of two, self excluded)
    a = _row(rows, "A", "France")
    assert a["peers"] == 2
    assert a["peer_median"] == 1.35
    assert a["eff_price"] == 1.50
    # B's peers are {A=1.50, C=1.30} -> median 1.40
    assert _row(rows, "B", "France")["peer_median"] == 1.40
    # C's peers are {A=1.50, B=1.40} -> median 1.45
    assert _row(rows, "C", "France")["peer_median"] == 1.45


def test_gap_and_addressable_eur(pi):
    rows, _ = pi.peer_benchmark("2026-05", "month", "Diesel")
    a = _row(rows, "A", "France")   # eff 1.50 vs peer 1.35 -> gap +0.15, pays above
    assert a["gap"] == 0.15
    assert a["addressable_eur"] == round(0.15 * 1000, 2)   # 150.00
    assert a["suppressed"] is False
    # C is BELOW its peer median (1.30 vs 1.45) -> gap negative, addressable 0.0
    c = _row(rows, "C", "France")
    assert c["gap"] == -0.15
    assert c["addressable_eur"] == 0.0


def test_total_addressable_sums_positive_gaps_only(pi):
    rows, summ = pi.peer_benchmark("2026-05", "month", "Diesel")
    # France: only A (gap +0.15 -> 150.0) and B (eff 1.40 vs peer 1.40 -> gap 0 -> 0.0)
    # contribute; C is negative. Spain cells are suppressed (no addressable).
    a = _row(rows, "A", "France")
    b = _row(rows, "B", "France")
    assert b["gap"] == 0.0 and b["addressable_eur"] == 0.0
    assert summ["total_addressable_eur"] == a["addressable_eur"]   # 150.00


def test_small_cohort_is_suppressed(pi):
    rows, summ = pi.peer_benchmark("2026-05", "month", "Diesel")
    sa = _row(rows, "A", "Spain")
    sb = _row(rows, "B", "Spain")
    # exactly ONE other entity each -> below PEER_MIN_CONTRIBUTORS -> suppressed, no figure
    assert sa["suppressed"] is True and sb["suppressed"] is True
    assert sa["peers"] == 1
    assert sa["peer_median"] is None and sa["gap"] is None and sa["addressable_eur"] is None
    # both Spain rows counted as suppressed cells
    assert summ["suppressed_cells"] == 2
    # France (3 entities) + Spain (2 entities) = 2 distinct (country, bucket) cells
    assert summ["cells"] == 2


def test_off_period_straggler_groups_into_period_cell(tmp_path, monkeypatch):
    """bucket_period (month grain) buckets on the FILTERED period, not the raw date — an
    entity dated in the prior month but LOADED under 2026-05 must join the 2026-05 cell,
    not spawn a foreign date bucket that would fragment the peer cohort."""
    pi = _seed(tmp_path, monkeypatch, [
        ("2026-05", "A", "France", "BP", "Lille", "2026-05-10", "Diesel", 1000, 1500.0),
        ("2026-05", "B", "France", "BP", "Lille", "2026-05-11", "Diesel", 1000, 1400.0),
        # C is a straggler dated in APRIL but loaded under the 2026-05 period
        ("2026-05", "C", "France", "BP", "Lille", "2026-04-29", "Diesel", 1000, 1300.0),
    ])
    rows, summ = pi.peer_benchmark("2026-05", "month", "Diesel")
    # one cohesive cell (not fragmented) -> A sees BOTH B and C as peers
    assert summ["cells"] == 1
    a = _row(rows, "A", "France")
    assert a["peers"] == 2
    assert a["peer_median"] == 1.35   # median of {B=1.40, C=1.30}, straggler included


def test_min_contributors_override(pi):
    # raising the floor to 3 suppresses the France cell too (each entity has only 2 others)
    rows, summ = pi.peer_benchmark("2026-05", "month", "Diesel", min_contributors=3)
    assert _row(rows, "A", "France")["suppressed"] is True
    assert summ["suppressed_cells"] == 5   # all 3 France + 2 Spain rows


# ---------------------------------------------------------------- web surface
_PEER_ROWS = [
    {"entity": "A", "country": "France", "bucket": "2026-05", "eff_price": 1.50,
     "qty": 1000.0, "peers": 2, "peer_median": 1.35, "gap": 0.15,
     "addressable_eur": 150.0, "suppressed": False},
    # an injection string in a DB value proves the page escapes it
    {"entity": "<script>x</script>", "country": "Spain", "bucket": "2026-05",
     "eff_price": 1.20, "qty": 1000.0, "peers": 1, "peer_median": None, "gap": None,
     "addressable_eur": None, "suppressed": True},
]
_PEER_SUMM = {"total_addressable_eur": 150.0, "cells": 2, "suppressed_cells": 1}


def test_pricing_peer_card_renders_escaped_and_labels_suppressed(client, monkeypatch):
    import pricing_intelligence
    monkeypatch.setattr(pricing_intelligence, "peer_benchmark",
                        lambda *a, **k: (_PEER_ROWS, _PEER_SUMM))
    r = client.get("/pricing")
    assert r.status_code == 200, r.status_code
    body = r.get_data(as_text=True)
    # headline addressable total rendered
    assert "150" in body
    # the injection string is ESCAPED, never raw
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;x&lt;/script&gt;" in body
    # suppressed cell is labelled, not given a peer figure
    assert "cohort too small" in body
    # NET-EUR/L basis is stated on the surface
    assert "NET EUR/L" in body
