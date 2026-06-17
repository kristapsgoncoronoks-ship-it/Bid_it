"""Embedded-finance ORIGINATION (finance.py, deepened) — the per-claim financeable model
+ the origination ledger lifecycle + the Financing admin page, all ADVISORY / ORIGINATION-
ONLY (NullProvider default, no funds move) and STRUCTURALLY non-mutating of the VAT engine.

Asserts:
  * financeable_offers() computes advance / fee / net-now / net-later / expected-payout
    from a fixture recovery report, EXCLUDING ineligible (draft/paid) claims and INCLUDING
    eligible (submitted/approved, filed-unpaid) ones; money.f2 rounding;
  * offer_advance() writes an `offered` row (audited, tenant-stamped, provider="null");
    set_status() transitions it; NullProvider never funds (status stays modelled);
  * the /financing page renders the offers table + ledger + disclaimer and escapes a
    planted XSS payload, and is admin-gated;
  * a GUARD test that the finance module NEVER calls a vat_refund write / never mutates a
    VAT figure or status — it only reads recovery_report() and writes its own finance.db.

Claims live in the app-owned vat_claims.db (vat_refund.connect()); we isolate it to a
tmp DB and point finance.DB at its own tmp file so nothing touches the demo DBs."""
import datetime
import importlib
import sqlite3

import pytest


def _iso(days_ago):
    return (datetime.date.today() - datetime.timedelta(days=days_ago)).isoformat()


def _seed_claims(tmp_path, monkeypatch):
    """Isolate vat_claims.db to tmp and seed claims at several statuses. Returns the
    reloaded vat_refund module bound to the tmp DB."""
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

    # ELIGIBLE (financeable): submitted + approved
    ins("ACME", "Germany", "2026-Q1", 1000.0, "submitted", "2", submitted=_iso(45))
    ins("ACME", "Germany", "2026-Q2", 500.0, "approved", "3", submitted=_iso(10))
    # INELIGIBLE: paid (already collected from the state)
    ins("ACME", "Germany", "2026-Q3", 800.0, "paid", "3A",
        submitted="2026-01-01", paid="2026-01-31", paid_amount=800.0)
    con.commit(); con.close()
    return vat_refund


def _fresh_finance(tmp_path, monkeypatch):
    import finance
    importlib.reload(finance)
    monkeypatch.setattr(finance, "DB", str(tmp_path / "finance.db"))
    finance._READY.clear()
    return finance


# ----------------------------------------------------- financeable_offers model

def test_financeable_offers_model_and_eligibility(tmp_path, monkeypatch):
    VR = _seed_claims(tmp_path, monkeypatch)
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: d)   # defaults 0.80 / 0.08

    fo = fin.financeable_offers()
    offers = fo["offers"]
    # eligible only: the two submitted/approved claims, NOT the paid one
    keys = {(o["entity"], o["period"], o["status"]) for o in offers}
    assert keys == {("ACME", "2026-Q1", "submitted"), ("ACME", "2026-Q2", "approved")}
    assert all(o["status"] in ("submitted", "approved") for o in offers)

    # hand-checked economics for the 1000 EUR submitted claim (expected_days=120):
    #   advance = 0.80 * 1000           = 800.00
    #   fee     = 0.08 * 800 * 120/365  = 21.04  (money.f2, ROUND_HALF_UP)
    #   net_now = 800 - 21.04           = 778.96
    #   net_later = 1000.00
    o1 = next(o for o in offers if o["period"] == "2026-Q1")
    assert o1["eligible_eur"] == 1000.00
    assert o1["advance_eur"] == 800.00
    assert o1["fee_eur"] == 21.04
    assert o1["net_now_eur"] == 778.96
    assert o1["net_later_eur"] == 1000.00
    assert o1["expected_days"] == 120
    assert o1["subject"] == "ACME|Germany|2026-Q1"

    # approved claim uses the shorter expected_days (45) → smaller fee
    o2 = next(o for o in offers if o["period"] == "2026-Q2")
    assert o2["expected_days"] == 45
    assert o2["advance_eur"] == 400.00
    # 0.08 * 400 * 45/365 = 3.945... -> 3.95
    assert o2["fee_eur"] == 3.95

    # totals sum the offers (money.fsum)
    assert fo["totals"]["eligible_eur"] == 1500.00
    assert fo["totals"]["advance_eur"] == 1200.00
    assert fo["totals"]["net_later_eur"] == 1500.00


def test_offer_for_is_pure_and_safe_on_bad_amount(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: d)
    econ = fin.offer_for(None, "submitted")
    assert econ["advance_eur"] == 0.0 and econ["fee_eur"] == 0.0


def test_financeable_offers_never_raises_on_broken_recovery(monkeypatch):
    import finance

    def boom(year=None):
        raise RuntimeError("recovery exploded")

    monkeypatch.setattr("vat_refund.recovery_report", boom)
    fo = finance.financeable_offers()
    assert fo["offers"] == [] and fo["totals"]["advance_eur"] == 0.0


def test_is_eligible_rule():
    import finance
    assert finance.is_eligible("submitted") is True
    assert finance.is_eligible("approved") is True
    for s in ("draft", "ready", "paid", "withdrawn", None, ""):
        assert finance.is_eligible(s) is False


# ----------------------------------------------------- origination ledger lifecycle

def test_offer_advance_records_offered_row_with_null_provider(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: d)
    row = fin.offer_advance("ACME|Germany|2026-Q1", 1000.0, 800.0, 21.04,
                            actor="pytest", period="2026-Q1", country="Germany")
    assert row is not None
    assert row["status"] == "offered"
    assert row["provider"] == "null"            # NullProvider — modelled, never funded
    assert row["eligible_eur"] == 1000.0
    assert row["amount_eur"] == 800.0
    assert row["fee_eur"] == 21.04
    assert row["created_by"] == "pytest"
    # advance(subject) returns the latest row for that receivable
    assert fin.advance("ACME|Germany|2026-Q1")["id"] == row["id"]


def test_offer_advance_is_audited(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: d)
    row = fin.offer_advance("ACME|Germany|2026-Q1", 1000.0, 800.0, 21.04, actor="auditor")
    con = fin.connect()
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM audit_log WHERE tbl='advances' AND action='offer_advance'"
        ).fetchone()[0]
    finally:
        con.close()
    assert n == 1


def test_set_status_transitions_and_rejects_unknown(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: d)
    row = fin.offer_advance("ACME|Germany|2026-Q1", 1000.0, 800.0, 21.04, actor="pytest")
    upd = fin.set_status(row["id"], "accepted", actor="pytest")
    assert upd["status"] == "accepted"
    # unknown status is refused (no mutation, returns None)
    assert fin.set_status(row["id"], "not_a_status") is None
    assert fin.advance("ACME|Germany|2026-Q1")["status"] == "accepted"
    # NullProvider never funds through any platform action — the status only moves when a
    # human explicitly transitions it; it never auto-advances to funded/repaid.


def test_null_provider_offer_stays_modelled(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: d)
    # default provider is Null; offering never funds — provider stamped "null", status offered
    row = fin.offer_advance("X|Y|Z", 100.0, 80.0, 2.0)
    assert row["provider"] == "null" and row["status"] == "offered"
    assert isinstance(fin.provider(), fin.NullProvider)


def test_tenant_stamp_on_offer(tmp_path, monkeypatch):
    """OFF (default) stamps the implicit 'default' tenant — byte-identical to today."""
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: d)
    fin.offer_advance("ACME|Germany|2026-Q1", 1000.0, 800.0, 21.04, actor="pytest")
    con = fin.connect()
    try:
        tid = con.execute("SELECT tenant_id FROM advances").fetchone()[0]
    finally:
        con.close()
    assert tid == "default"


# ------------------------------------------------- STRUCTURAL no-mutation guard

def test_finance_never_calls_a_vat_refund_write(tmp_path, monkeypatch):
    """The cardinal guardrail: finance ORIGINATION reads recovery_report() and writes ONLY
    its own finance.db — it must NEVER call a vat_refund mutation. We poison every
    vat_refund write/lifecycle entrypoint so any call would explode, then exercise the
    whole origination path."""
    VR = _seed_claims(tmp_path, monkeypatch)
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: d)

    import vat_refund

    def forbidden(*a, **k):
        raise AssertionError("finance must NEVER call a vat_refund write path")

    # poison every plausible VAT-mutating entrypoint
    for name in ("set_status", "submit_claim", "withdraw_claim", "record_decision",
                 "record_payment", "build_claim", "save_application", "set_fee",
                 "lock_invoices", "unlock_invoices", "update_application"):
        if hasattr(vat_refund, name):
            monkeypatch.setattr(vat_refund, name, forbidden, raising=False)

    # snapshot the VAT claims BEFORE
    con = vat_refund.connect()
    before = con.execute(
        "SELECT entity, ref_period, vat_eur, status, status_code FROM vat_applications "
        "ORDER BY entity, refund_country, ref_period").fetchall()
    before = [tuple(r) for r in before]
    con.close()

    # exercise the full origination path
    fo = fin.financeable_offers()
    o = fo["offers"][0]
    row = fin.offer_advance(o["subject"], o["eligible_eur"], o["advance_eur"], o["fee_eur"],
                            actor="guard", period=o["period"], country=o["country"])
    fin.set_status(row["id"], "accepted", actor="guard")

    # VAT claims are byte-identical AFTER — finance touched no figure / status
    con = vat_refund.connect()
    after = con.execute(
        "SELECT entity, ref_period, vat_eur, status, status_code FROM vat_applications "
        "ORDER BY entity, refund_country, ref_period").fetchall()
    after = [tuple(r) for r in after]
    con.close()
    assert before == after


# ----------------------------------------------------------------- web surface

def test_financing_page_renders_offers_ledger_and_disclaimer(client, tmp_path, monkeypatch):
    import finance
    monkeypatch.setattr(finance, "DB", str(tmp_path / "finance.db"))
    finance._READY.clear()
    # record an advance so the ledger table renders a row
    finance.offer_advance("ACME|Germany|2026-Q1", 1000.0, 800.0, 21.04, actor="pytest",
                          period="2026-Q1", country="Germany")

    r = client.get("/financing")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # disclaimer
    assert "Advisory / origination-only" in html
    assert "no funds move and this is not a financing offer" in html
    assert "licensed partner" in html.lower()
    # offers section + ledger section
    assert "Financeable receivables" in html
    assert "Origination ledger" in html
    assert "net now" in html.lower() and "net later" in html.lower()
    # the recorded advance row is visible (escaped)
    assert "ACME|Germany|2026-Q1" in html
    assert "800.00" in html


def test_financing_page_escapes_planted_xss(client, tmp_path, monkeypatch):
    """A malicious entity name from the recovery data must be escaped on the page."""
    import finance
    monkeypatch.setattr(finance, "DB", str(tmp_path / "finance.db"))
    finance._READY.clear()
    xss = '<script>alert(1)</script>'

    def fake_offers(report=None):
        return {
            "offers": [{
                "subject": xss, "entity": xss, "country": "DE", "period": "2026-Q1",
                "status": "submitted", "age_days": 10,
                "eligible_eur": 1000.0, "advance_eur": 800.0, "fee_eur": 21.04,
                "net_now_eur": 778.96, "net_later_eur": 1000.0,
                "expected_days": 120, "expected_payout": "2026-05-01"}],
            "totals": {"eligible_eur": 1000.0, "advance_eur": 800.0, "fee_eur": 21.04,
                       "net_now_eur": 778.96, "net_later_eur": 1000.0},
            "terms": {"advance_rate": 0.80, "fee_rate_annual": 0.08}}

    monkeypatch.setattr(finance, "financeable_offers", fake_offers)
    r = client.get("/financing")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert xss not in html                       # raw payload never present
    assert "&lt;script&gt;" in html              # escaped form is


def test_financing_is_admin_only(admin_session):
    import app as A, auth
    auth.add_user("finproc2", "Pw!23456", role="processor")
    cp = A.app.test_client()
    cp.post("/login", data={"username": "finproc2", "password": "Pw!23456"})
    assert cp.get("/financing").status_code == 403
