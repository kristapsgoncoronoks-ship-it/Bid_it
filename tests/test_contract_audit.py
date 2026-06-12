"""Contract-compliance auditor: invoiced lines vs contracted discount terms, with
recoverable EUR per breach."""
import importlib

import pytest


@pytest.fixture()
def ca(tmp_path, monkeypatch):
    import supplier_master, contract_audit
    importlib.reload(supplier_master)
    importlib.reload(contract_audit)
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    supplier_master._SCHEMA_READY.clear()
    monkeypatch.setattr(contract_audit, "DB", str(tmp_path / "fuel_history.db"))
    # transactions store the auditor reads
    con = contract_audit._con()
    con.execute("""CREATE TABLE IF NOT EXISTS transactions (
        period TEXT, supplier TEXT, country TEXT, station TEXT, product_group TEXT,
        qty REAL, net_eur REAL, net_eur_eff REAL)""")
    con.executemany("""INSERT INTO transactions
        (period,supplier,country,station,product_group,qty,net_eur,net_eur_eff)
        VALUES (?,?,?,?,?,?,?,?)""", [
        # TFC Belgium hub 'Meer': 1000 L at 1.50 doc, NO rebate applied (eff=doc)
        ("2026-05", "TFC", "Belgium", "Meer", "Diesel", 1000, 1500.0, 1500.0),
        # TFC third-party 'Romac': not a hub -> no discount expected
        ("2026-05", "TFC", "Belgium", "Romac", "Diesel", 200, 320.0, 320.0),
        # DKV Sweden: eff price 1.60 EUR/L (over a 1.50 ceiling)
        ("2026-05", "DKV", "Sweden", "Stockholm", "Diesel", 500, 800.0, 800.0),
    ])
    con.commit(); con.close()
    return supplier_master, contract_audit


def test_short_discount_flagged(ca):
    SM, CA = ca
    SM.set_discount_rule("TFC", country="Belgium", station_like="%Meer%",
                         expected_discount_eur_l=0.205, note="hub discount")
    flags, summ = CA.audit("2026-05")
    f = next(f for f in flags if f["supplier"] == "TFC")
    assert f["issue"] == "short discount" and f["station"] == "Meer"
    # recover = (0.205 - 0) * 1000 = 205
    assert f["recover_eur"] == 205.0
    assert summ["total_recover"] == 205.0
    # the non-hub 'Romac' line is NOT flagged (station_like didn't match)
    assert all(x["station"] != "Romac" for x in flags)


def test_over_ceiling_flagged(ca):
    SM, CA = ca
    SM.set_discount_rule("DKV", country="Sweden", max_net_eur_l=1.50, note="price cap")
    flags, summ = CA.audit("2026-05")
    f = next(f for f in flags if f["supplier"] == "DKV")
    assert f["issue"] == "over ceiling"
    # eff 1.60 over 1.50 -> (0.10) * 500 = 50
    assert f["recover_eur"] == 50.0


def test_no_rules_no_flags(ca):
    SM, CA = ca
    flags, summ = CA.audit("2026-05")
    assert flags == [] and summ["total_recover"] == 0.0


def test_like_matching(ca):
    _SM, CA = ca
    assert CA._like("Meer", "%Meer%") and CA._like("Meer 2", "%Meer%")
    assert CA._like("anything", "%") and CA._like("", "%")
    assert not CA._like("Romac", "%Meer%")
