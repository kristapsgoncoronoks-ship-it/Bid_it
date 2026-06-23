"""Per-country VAT recoverability (vat_entitlement.py): the deductibility layer that scales the
claimable VAT per country so a claim isn't over-stated. Advisory; default 100%."""
import pytest

import vat_entitlement as VE


@pytest.fixture()
def _settings(monkeypatch):
    store = {}
    import auth
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(auth, "set_setting", lambda k, v: store.__setitem__(k, v))
    return store


def test_default_full_recoverability(_settings):
    assert VE.recoverable_pct("Belgium") == 100.0
    assert VE.recoverable_eur("Belgium", 100) == 100.0


def test_admin_override_and_clear_and_validate(_settings):
    ok, _ = VE.set_recoverable_pct("Belgium", 50)
    assert ok and VE.recoverable_pct("Belgium") == 50.0
    assert VE.recoverable_eur("Belgium", 100) == 50.0
    assert VE.configured_countries().get("Belgium") == 50.0   # haircut country flagged
    # out-of-range and non-numeric rejected
    assert VE.set_recoverable_pct("Belgium", 150)[0] is False
    assert VE.set_recoverable_pct("Belgium", "x")[0] is False
    # clearing reverts to 100
    VE.set_recoverable_pct("Belgium", None)
    assert VE.recoverable_pct("Belgium") == 100.0


def test_recoverable_summary_applies_per_country(monkeypatch, _settings):
    import vat_refund
    monkeypatch.setattr(vat_refund, "claims_overview", lambda y: {
        "to_submit": [{"country": "Belgium", "vat_eur": 1000.0}],
        "open": [{"country": "Germany", "vat_eur": 500.0}]})
    VE.set_recoverable_pct("Belgium", 50)            # BE half-recoverable, DE full
    s = VE.recoverable_summary("2026")
    assert s["gross_eur"] == 1500.0
    assert s["recoverable_eur"] == 1000.0            # 500 (BE) + 500 (DE)
    assert s["haircut_eur"] == 500.0
    be = next(b for b in s["by_country"] if b["country"] == "Belgium")
    assert be["haircut"] == 500.0 and be["pct"] == 50.0


def test_entitlement_page_renders(client):
    body = client.get("/entitlement").get_data(as_text=True)
    assert "Per-country VAT recoverability" in body and "fuel-card rule" in body
