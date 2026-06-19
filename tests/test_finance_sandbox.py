"""Embedded-finance SANDBOX provider (finance.py) — the pluggable-provider architecture
exercised end-to-end WITHOUT a live partner.

Asserts:
  * the FinanceProvider protocol (quote / accept / fund / repay / status) shapes;
  * SandboxFinanceProvider.quote() generates offer terms from the receivable outstanding
    (the same transparent offer_for economics);
  * a full accept → fund → repay lifecycle (and simulate()) advances the finance.db ledger
    states offered → accepted → funded → repaid — recording ONLY to finance.db;
  * finance_provider=null (the DEFAULT) FUNDS NOTHING — fund/repay are no-ops, status
    never reaches funded/repaid;
  * THE ADVISORY INVARIANT: a VAT claim's status + recovery figures are BYTE-IDENTICAL
    before and after a full sandbox advance lifecycle.

Claims live in the app-owned vat_claims.db (vat_refund.connect()); we isolate it to a tmp
DB and point finance.DB at its own tmp file so nothing touches the demo DBs."""
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

    ins("ACME", "Germany", "2026-Q1", 1000.0, "submitted", "2", submitted=_iso(45))
    ins("ACME", "Germany", "2026-Q2", 500.0, "approved", "3", submitted=_iso(10))
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


# ------------------------------------------------- provider selection / protocol

def test_sandbox_provider_selected_by_setting(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting",
                        lambda k, d=None: "sandbox" if k == "finance_provider" else d)
    p = fin.provider()
    assert isinstance(p, fin.SandboxFinanceProvider)
    assert p.name == "sandbox"


def test_default_provider_is_null(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: d)
    assert isinstance(fin.provider(), fin.NullProvider)


def test_sandbox_quote_generated_from_outstanding(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: d)   # defaults 0.80 / 0.08
    p = fin.SandboxFinanceProvider()
    # the sandbox prices the receivable with the transparent offer_for economics:
    #   advance = 0.80 * 1000           = 800.00
    #   fee     = 0.08 * 800 * 120/365  = 21.04
    q = p.quote(1000.0, "submitted")
    assert q["eligible_eur"] == 1000.00
    assert q["advance_eur"] == 800.00
    assert q["fee_eur"] == 21.04
    assert q["net_now_eur"] == 778.96
    assert q["net_later_eur"] == 1000.00


# ----------------------------------------------------- full sandbox lifecycle

def test_sandbox_full_lifecycle_advances_ledger(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting",
                        lambda k, d=None: "sandbox" if k == "finance_provider" else d)
    p = fin.provider()
    assert isinstance(p, fin.SandboxFinanceProvider)

    q = p.quote(1000.0, "submitted")
    row = fin.offer_advance("ACME|Germany|2026-Q1", q["eligible_eur"], q["advance_eur"],
                            q["fee_eur"], actor="pytest", period="2026-Q1", country="Germany")
    aid = row["id"]
    assert fin.advance_by_id(aid)["status"] == "offered"
    assert fin.advance_by_id(aid)["provider"] == "sandbox"

    assert p.accept(aid, actor="pytest")["status"] == "accepted"
    assert p.status(aid) == "accepted"
    assert p.fund(aid, actor="pytest")["status"] == "funded"
    assert p.repay(aid, actor="pytest")["status"] == "repaid"
    assert fin.advance_by_id(aid)["status"] == "repaid"


def test_sandbox_simulate_runs_whole_lifecycle(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting",
                        lambda k, d=None: "sandbox" if k == "finance_provider" else d)
    p = fin.provider()
    row = fin.offer_advance("ACME|Germany|2026-Q1", 1000.0, 800.0, 21.04, actor="pytest")
    final = p.simulate(row["id"], actor="pytest")
    assert final["status"] == "repaid"
    assert fin.advance_by_id(row["id"])["status"] == "repaid"


def test_sandbox_fund_refuses_unaccepted_offer(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting",
                        lambda k, d=None: "sandbox" if k == "finance_provider" else d)
    p = fin.provider()
    row = fin.offer_advance("X|Y|Z", 100.0, 80.0, 2.0)
    # cannot fund an offer that was never accepted
    assert p.fund(row["id"]) is None
    assert fin.advance_by_id(row["id"])["status"] == "offered"
    # cannot repay a not-yet-funded advance
    p.accept(row["id"])
    assert p.repay(row["id"]) is None
    assert fin.advance_by_id(row["id"])["status"] == "accepted"


# ---------------------------------------------- null provider funds NOTHING

def test_null_provider_funds_nothing(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: d)   # default -> null
    p = fin.provider()
    assert isinstance(p, fin.NullProvider)
    row = fin.offer_advance("ACME|Germany|2026-Q1", 1000.0, 800.0, 21.04)
    aid = row["id"]
    fin.set_status(aid, "accepted")
    # NullProvider.fund/repay are NO-OPS: the status never advances to funded/repaid
    assert p.fund(aid)["status"] == "accepted"
    assert p.repay(aid)["status"] == "accepted"
    assert fin.advance_by_id(aid)["status"] == "accepted"


# ---------------------- THE ADVISORY INVARIANT: claim unchanged after lifecycle

def test_claim_unchanged_after_full_sandbox_lifecycle(tmp_path, monkeypatch):
    """PROOF of the cardinal invariant: a full sandbox advance lifecycle (offered →
    accepted → funded → repaid) touches finance.db ONLY — the VAT claim's status and
    recovery figures are BYTE-IDENTICAL before and after."""
    VR = _seed_claims(tmp_path, monkeypatch)
    fin = _fresh_finance(tmp_path, monkeypatch)
    monkeypatch.setattr("auth.get_setting",
                        lambda k, d=None: "sandbox" if k == "finance_provider" else d)

    # poison every plausible VAT-mutating entrypoint — any call would explode
    import vat_refund

    def forbidden(*a, **k):
        raise AssertionError("sandbox lifecycle must NEVER call a vat_refund write path")

    for name in ("set_status", "submit_claim", "withdraw_claim", "record_decision",
                 "record_payment", "build_claim", "save_application", "set_fee",
                 "lock_invoices", "unlock_invoices", "update_application"):
        if hasattr(vat_refund, name):
            monkeypatch.setattr(vat_refund, name, forbidden, raising=False)

    # snapshot the claim rows AND the recovery report BEFORE
    con = vat_refund.connect()
    before_rows = [tuple(r) for r in con.execute(
        "SELECT entity, refund_country, ref_period, vat_eur, status, status_code, "
        "submitted_date, paid_date, paid_amount FROM vat_applications "
        "ORDER BY entity, refund_country, ref_period").fetchall()]
    con.close()
    before_out, before_summary = VR.recovery_report("2026")

    # run the WHOLE sandbox lifecycle on a financeable receivable
    p = fin.provider()
    fo = fin.financeable_offers()
    o = fo["offers"][0]
    row = fin.offer_advance(o["subject"], o["eligible_eur"], o["advance_eur"], o["fee_eur"],
                            actor="invariant", period=o["period"], country=o["country"])
    p.accept(row["id"], actor="invariant")
    p.fund(row["id"], actor="invariant")
    p.repay(row["id"], actor="invariant")
    assert fin.advance_by_id(row["id"])["status"] == "repaid"

    # the VAT claim rows + recovery figures are byte-identical AFTER
    con = vat_refund.connect()
    after_rows = [tuple(r) for r in con.execute(
        "SELECT entity, refund_country, ref_period, vat_eur, status, status_code, "
        "submitted_date, paid_date, paid_amount FROM vat_applications "
        "ORDER BY entity, refund_country, ref_period").fetchall()]
    con.close()
    after_out, after_summary = VR.recovery_report("2026")

    assert before_rows == after_rows
    assert before_summary == after_summary
    assert before_out == after_out


# ----------------------------------------------------------------- web surface

def test_financing_page_sandbox_lifecycle_buttons(client, tmp_path, monkeypatch):
    import finance, auth
    monkeypatch.setattr(finance, "DB", str(tmp_path / "finance.db"))
    finance._READY.clear()
    auth.set_setting("finance_provider", "sandbox")
    try:
        finance.offer_advance("ACME|Germany|2026-Q1", 1000.0, 800.0, 21.04, actor="pytest",
                              period="2026-Q1", country="Germany")
        r = client.get("/financing")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "sandbox" in html
        # offered -> accepted/declined transitions; simulate button present under sandbox
        assert "simulate" in html
    finally:
        auth.set_setting("finance_provider", "none")
