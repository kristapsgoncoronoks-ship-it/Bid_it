"""VAT receivables / payout-forecast view (vat_refund.receivables_forecast) — the
INTERNAL, data-only financing-ready surface combining the under-used VAT-lifecycle
data (DATA_ARCHITECTURE.md #2 cycle-time/forecast, #9 realization rate).

Claims live in the app-owned vat_claims.db (vat_refund.connect()); we isolate it to a
tmp DB and seed claims at several statuses (both the default 'customer' route and the
'us' deduct route) with submitted/approved/paid dates and a partial paid_amount, then
assert the ROUTE-AWARE settlement figures (refund receivable / agency fee / customer
net) / aging / cycle-time / realization / open-forecast math. Plus the page renders 200
for an admin (escaped) and is admin-gated (a processor is blocked)."""
import datetime
import importlib
import sqlite3

import pytest


def _iso(days_ago):
    return (datetime.date.today() - datetime.timedelta(days=days_ago)).isoformat()


def _seed(tmp_path, monkeypatch):
    """Isolate the claims DB to tmp and seed a fixed set of claims. Returns the
    reloaded vat_refund module bound to the tmp DB."""
    import vat_refund
    importlib.reload(vat_refund)
    claims = tmp_path / "vat_claims.db"
    analytics = tmp_path / "fuel_history.db"
    # analytics DB just needs to exist with a transactions table (not read here)
    a = sqlite3.connect(str(analytics))
    a.execute("CREATE TABLE transactions (entity, country, currency, period, vat_eur, vat_local)")
    a.commit(); a.close()

    monkeypatch.setattr(vat_refund, "DB", str(claims))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(analytics))
    vat_refund._SCHEMA_READY.clear()
    con = vat_refund.connect()

    def ins(entity, ctry, period, vat, fee, status, code,
            submitted=None, approved=None, paid=None, paid_amount=None, payout_to=None):
        con.execute(
            """INSERT INTO vat_applications
               (entity, refund_country, ref_period, vat_eur, currency, status, status_code,
                fee_eur, submitted_date, approved_date, paid_date, paid_amount, payout_to)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (entity, ctry, period, vat, "EUR", status, code, fee,
             submitted, approved, paid, paid_amount, payout_to))

    # OPEN, submitted 45 days ago -> band 30-60. DEFAULT route (payout_to NULL ==
    # 'customer'): refund receivable = vat = 1000, agency fee = 100, customer net = 0.
    ins("ACME", "Germany", "2026-Q1", 1000.0, 100.0, "submitted", "2",
        submitted=_iso(45))
    # OPEN, approved, submitted 10 days ago -> band 0-30. EXPLICIT 'customer' route:
    # refund receivable = 500, fee = 50, customer net = 0.
    ins("ACME", "Germany", "2026-Q2", 500.0, 50.0, "approved", "3",
        submitted=_iso(10), approved=_iso(3), payout_to="customer")
    # OPEN, submitted 200 days ago -> band 90+. EXPLICIT 'us' deduct route: refund
    # receivable = 2000 (route-independent), fee = 200, customer net = 2000-200 = 1800.
    ins("BETA", "Poland", "2026-Q1", 2000.0, 200.0, "submitted", "2",
        submitted=_iso(200), payout_to="us")
    # PAID, full realization, Germany, 30-day cycle; refund receivable 800.
    ins("ACME", "Germany", "2026-Q3", 800.0, 80.0, "paid", "3A",
        submitted="2026-01-01", paid="2026-01-31", paid_amount=800.0)
    # PAID, HAIRCUT (partial), Germany, 50-day cycle; realization 600/800
    ins("GAMMA", "Germany", "2026-Q4", 800.0, 80.0, "paid", "3A",
        submitted="2026-02-01", paid="2026-03-23", paid_amount=600.0)
    # PAID, Poland, 100-day cycle; full realization 2000/2000
    ins("BETA", "Poland", "2026-Q3", 2000.0, 200.0, "paid", "3A",
        submitted="2026-01-01", paid="2026-04-11", paid_amount=2000.0)
    con.commit(); con.close()
    return vat_refund


def test_per_claim_figures_match_settlement_for_both_routes(tmp_path, monkeypatch):
    """Every row's route-aware figures must equal vat_refund.settlement(route, vat, fee)
    — the SAME helper the Recovery page uses — for BOTH the default 'customer' route and
    the 'us' deduct route, so the two cash flows are never conflated."""
    VR = _seed(tmp_path, monkeypatch)
    fc = VR.receivables_forecast("2026")
    for r in fc["rows"]:
        st = VR.settlement(r["route"], r["vat_eur"], r["fee_eur"])
        # refund receivable = vat (route-independent — what the state owes / is aged)
        assert r["refund_receivable_eur"] == st["refund"] == VR.money.f2(r["vat_eur"]), r
        # customer net follows the route
        assert r["net_to_customer_eur"] == st["net_to_customer"], r

    by = {(r["entity"], r["period"]): r for r in fc["rows"]}
    # DEFAULT route (NULL -> 'customer'): refund receivable = vat, fee = fee, net = 0
    acme_q1 = by[("ACME", "2026-Q1")]
    assert acme_q1["route"] == "customer"
    assert acme_q1["refund_receivable_eur"] == 1000.0
    assert acme_q1["fee_eur"] == 100.0
    assert acme_q1["net_to_customer_eur"] == 0.0
    # EXPLICIT 'customer' route: same shape — customer collects the full refund, fee invoiced
    acme_q2 = by[("ACME", "2026-Q2")]
    assert acme_q2["route"] == "customer"
    assert acme_q2["refund_receivable_eur"] == 500.0
    assert acme_q2["net_to_customer_eur"] == 0.0
    # EXPLICIT 'us' deduct route: refund receivable still = vat, but net_to_customer = vat-fee
    beta_q1 = by[("BETA", "2026-Q1")]
    assert beta_q1["route"] == "us"
    assert beta_q1["refund_receivable_eur"] == 2000.0
    assert beta_q1["fee_eur"] == 200.0
    assert beta_q1["net_to_customer_eur"] == VR.money.f2(2000.0 - 200.0) == 1800.0


def test_aging_bands(tmp_path, monkeypatch):
    VR = _seed(tmp_path, monkeypatch)
    fc = VR.receivables_forecast("2026")
    band = {(r["entity"], r["period"]): r["aging_band"] for r in fc["rows"]}
    assert band[("ACME", "2026-Q1")] == "30-60"   # 45 days
    assert band[("ACME", "2026-Q2")] == "0-30"    # 10 days
    assert band[("BETA", "2026-Q1")] == "90+"     # 200 days
    # paid claims carry no open-aging band
    assert band[("ACME", "2026-Q3")] == ""
    # Aging is on the REFUND RECEIVABLE (vat owed by the state), route-independent —
    # NOT vat-fee. ACME-Q1 vat=1000, ACME-Q2 vat=500, BETA-Q1 ('us') vat=2000.
    by = fc["aging"]["by_band"]
    assert by["30-60"]["count"] == 1 and by["30-60"]["eur"] == 1000.0
    assert by["0-30"]["count"] == 1 and by["0-30"]["eur"] == 500.0
    assert by["90+"]["count"] == 1 and by["90+"]["eur"] == 2000.0
    assert fc["aging"]["total_count"] == 3
    assert fc["aging"]["total_eur"] == VR.money.f2(1000.0 + 500.0 + 2000.0)


def test_cycle_time_median_per_country(tmp_path, monkeypatch):
    VR = _seed(tmp_path, monkeypatch)
    fc = VR.receivables_forecast("2026")
    ct = fc["cycle_time"]
    # Germany paid claims: 30 and 50 days -> median 40
    assert ct["by_country"]["Germany"] == 40.0
    # Poland: a single 100-day paid claim
    assert ct["by_country"]["Poland"] == 100.0
    # overall: [30, 50, 100] -> median 50
    assert ct["overall"] == 50.0


def test_realization_rate_paid_over_claimed(tmp_path, monkeypatch):
    VR = _seed(tmp_path, monkeypatch)
    fc = VR.receivables_forecast("2026")
    rz = fc["realization"]
    # Germany paid: claimed 800+800=1600, paid 800+600=1400 -> 0.875
    assert rz["Germany"]["claimed"] == 1600.0
    assert rz["Germany"]["paid"] == 1400.0
    assert rz["Germany"]["rate"] == pytest.approx(1400.0 / 1600.0)
    # Poland paid: 2000/2000 -> 1.0
    assert rz["Poland"]["rate"] == pytest.approx(1.0)
    # overall paid 1400+2000=3400 / claimed 1600+2000=3600
    assert rz["overall"]["claimed"] == 3600.0
    assert rz["overall"]["paid"] == 3400.0
    assert rz["overall"]["rate"] == pytest.approx(3400.0 / 3600.0)


def test_open_forecast_keeps_refund_and_fee_separate_across_routes(tmp_path, monkeypatch):
    """The open-receivable forecast aggregates two SEPARATE flows and never sums vat-fee
    across the mixed routes: the REFUND RECEIVABLE (vat owed by the state, route-
    independent) and the AGENCY FEE RECEIVABLE (the frozen fee), kept apart."""
    VR = _seed(tmp_path, monkeypatch)
    fc = VR.receivables_forecast("2026")
    f = fc["forecast"]
    # the 3 open claims (ACME-Q1 customer, ACME-Q2 customer, BETA-Q1 us)
    assert f["open_count"] == 3
    # refund receivable = sum of vat of open claims (route-independent): 1000+500+2000
    assert f["open_refund_receivable_eur"] == VR.money.f2(1000.0 + 500.0 + 2000.0)
    # agency fee receivable = sum of frozen fees of open claims: 100+50+200
    assert f["open_fee_receivable_eur"] == VR.money.f2(100.0 + 50.0 + 200.0)
    # weighted refund: Germany open refunds (1000+500) * 0.875 + Poland open 2000 * 1.0
    expected_w = VR.money.f2(1000.0 * (1400.0 / 1600.0))
    expected_w = VR.money.f2(expected_w + 500.0 * (1400.0 / 1600.0))
    expected_w = VR.money.f2(expected_w + 2000.0 * 1.0)
    assert f["open_weighted_refund_eur"] == expected_w
    # forecast never includes a paid claim's refund (open total < open + a paid refund)
    assert f["open_refund_receivable_eur"] < (1000.0 + 500.0 + 2000.0 + 800.0)
    # the legacy conflated key is gone — no single vat-minus-fee figure across routes
    assert "open_expected_payout_eur" not in f
    assert "expected_payout_eur" not in fc["rows"][0]


# ----------------------------------------------------------------- web surface

def test_receivables_page_renders_200_for_admin(client):
    """The admin VAT receivables page renders against the live (possibly empty) claims
    DB and escapes its values."""
    r = client.get("/receivables")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "VAT receivables" in html
    assert "Open-receivable cash forecast" in html
    assert "Cycle time" in html


def test_receivables_is_admin_only(admin_session):
    """A processor is blocked from the receivables view and its export (admin-only)."""
    import app as A, auth
    auth.add_user("rcvproc", "Pw!23456", role="processor")
    cp = A.app.test_client()
    cp.post("/login", data={"username": "rcvproc", "password": "Pw!23456"})
    assert cp.get("/receivables").status_code == 403
    assert cp.get("/export/receivables").status_code == 403
    # and the nav link is hidden for a processor
    assert "Receivables &amp; forecast" not in cp.get("/").get_data(as_text=True)
