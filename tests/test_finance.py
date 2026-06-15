"""Embedded-finance seam (finance.py) — the ADDITIVE origination layer over the VAT
recovery data. Asserts:

  * financeable(year).total reconciles EXACTLY with recovery_report(year)'s
    summary["outstanding"] (it REUSES it — never a second, divergent sum), and the rows
    are the submitted/approved claims only (no paid/draft);
  * quote() advance economics (hand-checked) and terms() defaults + clamping;
  * NullProvider / provider() default behaviour;
  * the advances ledger (request_advance records a no_provider row; list_advances reads
    it back) and that the public read functions never raise on a missing/broken DB;
  * the /receivables Financing section renders for an admin and is admin-gated.

Claims live in the app-owned vat_claims.db (vat_refund.connect()); we isolate it to a
tmp DB (mirroring test_receivables) and point finance.DB at its own tmp file so nothing
touches the demo DBs. finance.financeable reuses recovery_report on that seeded DB."""
import datetime
import importlib
import sqlite3

import pytest


def _iso(days_ago):
    return (datetime.date.today() - datetime.timedelta(days=days_ago)).isoformat()


def _seed_claims(tmp_path, monkeypatch):
    """Isolate vat_claims.db to tmp and seed a known set of claims at several statuses.
    Returns the reloaded vat_refund module bound to the tmp DB."""
    import vat_refund
    importlib.reload(vat_refund)
    claims = tmp_path / "vat_claims.db"
    analytics = tmp_path / "fuel_history.db"
    a = sqlite3.connect(str(analytics))
    a.execute("CREATE TABLE transactions (entity, country, currency, period, vat_eur, vat_local)")
    a.commit(); a.close()

    monkeypatch.setattr(vat_refund, "DB", str(claims))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(analytics))
    vat_refund._SCHEMA_READY.clear()
    con = vat_refund.connect()

    def ins(entity, ctry, period, vat, status, code, submitted=None, paid=None,
            paid_amount=None):
        con.execute(
            """INSERT INTO vat_applications
               (entity, refund_country, ref_period, vat_eur, currency, status, status_code,
                submitted_date, paid_date, paid_amount)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (entity, ctry, period, vat, "EUR", status, code, submitted, paid, paid_amount))

    # OPEN (financeable): submitted + approved
    ins("ACME", "Germany", "2026-Q1", 1000.0, "submitted", "2", submitted=_iso(45))
    ins("ACME", "Germany", "2026-Q2", 500.0, "approved", "3", submitted=_iso(10))
    ins("BETA", "Poland", "2026-Q1", 2000.0, "submitted", "2", submitted=_iso(200))
    # PAID (NOT financeable — already collected from the state)
    ins("ACME", "Germany", "2026-Q3", 800.0, "paid", "3A",
        submitted="2026-01-01", paid="2026-01-31", paid_amount=800.0)
    con.commit(); con.close()
    return vat_refund


def _fresh_finance(tmp_path, monkeypatch):
    """Reload finance with its DB pointed at an isolated tmp file."""
    import finance
    importlib.reload(finance)
    monkeypatch.setattr(finance, "DB", str(tmp_path / "finance.db"))
    finance._READY.clear()
    return finance


# --------------------------------------------------------------- financeable

def test_financeable_total_reconciles_with_recovery_report(tmp_path, monkeypatch):
    VR = _seed_claims(tmp_path, monkeypatch)
    fin = _fresh_finance(tmp_path, monkeypatch)
    out, summary = VR.recovery_report("2026")
    f = fin.financeable("2026")
    # the total IS the recovery report's outstanding (reused, not re-summed differently)
    assert f["total"] == summary["outstanding"]
    # outstanding = the open submitted/approved receivable: 1000 + 500 + 2000
    assert f["total"] == VR.money.fsum([1000.0, 500.0, 2000.0])


def test_financeable_rows_are_open_claims_only(tmp_path, monkeypatch):
    VR = _seed_claims(tmp_path, monkeypatch)
    fin = _fresh_finance(tmp_path, monkeypatch)
    f = fin.financeable("2026")
    statuses = {r["status"] for r in f["rows"]}
    assert statuses == {"submitted", "approved"}        # no paid/draft
    keys = {(r["entity"], r["period"]) for r in f["rows"]}
    assert keys == {("ACME", "2026-Q1"), ("ACME", "2026-Q2"), ("BETA", "2026-Q1")}
    # rows expose the documented fields
    r0 = f["rows"][0]
    assert set(r0) == {"entity", "country", "period", "vat_eur", "status", "age_days"}


# --------------------------------------------------------------------- quote

def test_quote_hand_checked():
    import finance
    q = finance.quote(1000.00, {"advance_pct": 0.90, "fee_pct": 0.02})
    assert q["advance_eur"] == 900.00
    assert q["fee_eur"] == 20.00
    assert q["net_now_eur"] == 880.00
    assert q["remainder_on_settlement_eur"] == 100.00
    assert q["receivable_eur"] == 1000.00
    assert q["terms"] == {"advance_pct": 0.90, "fee_pct": 0.02}


def test_quote_is_pure_and_uses_configured_terms_by_default(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    q = fin.quote(1000.0)
    # defaults 0.90 / 0.02 → 900 / 20 / 880 / 100
    assert (q["advance_eur"], q["fee_eur"], q["net_now_eur"],
            q["remainder_on_settlement_eur"]) == (900.0, 20.0, 880.0, 100.0)
    # bad amount does not raise
    assert fin.quote(None)["advance_eur"] == 0.0


# --------------------------------------------------------------------- terms

def test_terms_defaults(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: d)
    t = fin.terms()
    assert t == {"advance_pct": 0.90, "fee_pct": 0.02}


def test_terms_clamps_out_of_range(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    settings = {"finance_advance_pct": "1.5", "finance_fee_pct": "0.9"}

    def fake_get(key, default=None):
        return settings.get(key, default)

    monkeypatch.setattr("auth.get_setting", fake_get)
    t = fin.terms()                                     # both out of range -> defaults
    assert t == {"advance_pct": 0.90, "fee_pct": 0.02}
    # advance_pct must be > 0 (0 rejected) and <= 1; fee_pct in [0, 0.5)
    settings["finance_advance_pct"] = "0"
    settings["finance_fee_pct"] = "0.5"
    assert fin.terms() == {"advance_pct": 0.90, "fee_pct": 0.02}
    # in-range values are honoured
    settings["finance_advance_pct"] = "0.8"
    settings["finance_fee_pct"] = "0.03"
    assert fin.terms() == {"advance_pct": 0.8, "fee_pct": 0.03}
    # unparseable -> default
    settings["finance_advance_pct"] = "abc"
    assert fin.terms()["advance_pct"] == 0.90


# ------------------------------------------------------------------ provider

def test_null_provider_and_default():
    import finance
    np = finance.NullProvider()
    res = np.submit_advance("ACME|Germany|2026-Q1", 900.0, {})
    assert res == {"ok": False, "ref": None, "status": "no_provider",
                   "message": "No financing partner configured — informational only."}
    # provider() defaults to NullProvider
    assert isinstance(finance.provider(), finance.NullProvider)


def test_provider_unknown_setting_falls_back(monkeypatch):
    import finance
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: "garbled-partner")
    assert isinstance(finance.provider(), finance.NullProvider)


# ------------------------------------------------------------- advances ledger

def test_request_advance_records_no_provider_row(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    res = fin.request_advance("ACME|Germany|2026-Q1", 900.0, 20.0, actor="pytest")
    assert res["status"] == "no_provider" and res["ok"] is False
    rows = fin.list_advances()
    assert len(rows) == 1
    r = rows[0]
    assert r["claim_key"] == "ACME|Germany|2026-Q1"
    assert r["amount_eur"] == 900.0 and r["fee_eur"] == 20.0
    assert r["provider"] == "none" and r["status"] == "no_provider"
    assert r["created_by"] == "pytest"


def test_list_advances_never_raises_on_missing_db(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    # DB points at a directory path that cannot be opened as a SQLite file
    monkeypatch.setattr(fin, "DB", str(tmp_path))       # a directory, not a file
    fin._READY.clear()
    assert fin.list_advances() == []                    # logged, not raised


def test_financeable_never_raises_on_broken_recovery(monkeypatch):
    import finance

    def boom(year=None):
        raise RuntimeError("recovery exploded")

    monkeypatch.setattr("vat_refund.recovery_report", boom)
    assert finance.financeable("2026") == {"rows": [], "total": 0.0}


# ----------------------------------------------------------------- web surface

def test_receivables_financing_section_renders_for_admin(client):
    r = client.get("/receivables")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Financing (embedded" in html
    assert "LICENSED factoring partner" in html
    assert "informational only" in html
    assert "net now" in html                            # the quote KPI is present


def test_receivables_financing_is_admin_only(admin_session):
    import app as A, auth
    auth.add_user("finproc", "Pw!23456", role="processor")
    cp = A.app.test_client()
    cp.post("/login", data={"username": "finproc", "password": "Pw!23456"})
    assert cp.get("/receivables").status_code == 403
