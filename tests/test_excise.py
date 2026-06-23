"""Diesel excise-duty refund estimate (excise.py): the parallel claim engine over the diesel
transactions in the ~7 EU refund countries. Advisory, admin-overridable rates."""
import excise


def test_refund_countries_and_rates():
    assert "Belgium" in excise.REFUND_COUNTRIES and "France" in excise.REFUND_COUNTRIES
    assert excise.is_refund_country("Belgium") is True
    assert excise.is_refund_country("Germany") is False        # not an excise-refund country
    assert excise.rate_for("Germany") is None
    assert excise.rate_for("Belgium") > 0


def test_admin_rate_override(monkeypatch):
    import auth
    store = {}
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(auth, "set_setting", lambda k, v: store.__setitem__(k, v))
    ok, err = excise.set_rate("France", 177.0)
    assert ok and excise.rate_for("France") == 177.0
    # clearing reverts to the indicative default
    excise.set_rate("France", "")
    assert excise.rate_for("France") == excise.REFUND_COUNTRIES["France"]
    # a non-refund country is rejected
    assert excise.set_rate("Germany", 100)[0] is False


def test_excise_report_only_refund_countries_and_diesel():
    rep = excise.excise_report()
    s = rep["summary"]
    assert s["recoverable_eur"] >= 0 and s["indicative"] is True
    # every row is an eligible country with a positive €, consistent with litres × rate
    for r in rep["rows"]:
        assert excise.is_refund_country(r["country"])
        assert r["recoverable_eur"] >= 0
        assert round(r["litres"] * r["rate_eur_per_1000l"] / 1000.0, 2) == r["recoverable_eur"]
    # the total matches the row sum and recoverable_total()
    assert round(sum(r["recoverable_eur"] for r in rep["rows"]), 2) == round(s["recoverable_eur"], 2)
    assert round(excise.recoverable_total(), 2) == round(s["recoverable_eur"], 2)


def test_excise_report_never_raises_on_bad_period():
    rep = excise.excise_report("not-a-period")
    assert rep["rows"] == [] and rep["summary"]["recoverable_eur"] == 0.0


def test_dashboard_shows_excise(client):
    r = client.get("/recovery-dashboard?year=2026")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "excise refund (est" in body
    assert "Diesel excise-duty refund" in body and "INDICATIVE rates" in body
