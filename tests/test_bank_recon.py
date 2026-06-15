"""
Tests for bank_recon.py — the open-banking reconciliation seam.

ADVISORY ONLY: reconciliation matches bank credits to expected VAT refunds (claims filed
but not paid) by amount + date and NEVER mutates a claim, status, lock or VAT figure.
"""
import os
import re
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import bank_recon as BR


# ----------------------------------------------------------------------------
# reconcile — the pure matcher
# ----------------------------------------------------------------------------
def test_reconcile_matches_within_tolerance():
    expected = [{"key": "E|LT|2026-Q1", "entity": "E", "country": "LT",
                 "period": "2026-Q1", "expected_eur": 1234.56, "since": "2026-03-01"}]
    bank = [{"date": "2026-03-10", "amount": 1234.56, "description": "VAT refund",
             "counterparty": "Tax authority"}]
    res = BR.reconcile(bank, expected)
    assert len(res["matched"]) == 1
    m = res["matched"][0]
    assert m["day_gap"] == 9
    assert m["amount_delta"] == 0.00
    assert m["expected"]["key"] == "E|LT|2026-Q1"
    assert res["unmatched_expected"] == []
    assert res["unmatched_bank"] == []


def test_reconcile_rejects_amount_out_of_tolerance():
    expected = [{"key": "k", "expected_eur": 1000.00, "since": "2026-03-01"}]
    bank = [{"date": "2026-03-05", "amount": 1000.50, "description": "", "counterparty": ""}]
    res = BR.reconcile(bank, expected, eur_tol=0.01)
    assert res["matched"] == []
    assert len(res["unmatched_expected"]) == 1
    assert len(res["unmatched_bank"]) == 1


def test_reconcile_rejects_credit_before_since():
    # a credit dated BEFORE the claim was filed cannot be that refund
    expected = [{"key": "k", "expected_eur": 500.00, "since": "2026-03-10"}]
    bank = [{"date": "2026-03-01", "amount": 500.00, "description": "", "counterparty": ""}]
    res = BR.reconcile(bank, expected)
    assert res["matched"] == []
    assert len(res["unmatched_expected"]) == 1


def test_reconcile_rejects_beyond_day_tol():
    expected = [{"key": "k", "expected_eur": 500.00, "since": "2026-03-01"}]
    bank = [{"date": "2026-04-30", "amount": 500.00, "description": "", "counterparty": ""}]
    res = BR.reconcile(bank, expected, day_tol=14)
    assert res["matched"] == []


def test_reconcile_one_to_one_picks_better_fit():
    # two expecteds, one bank line — only the closest-amount expected matches
    expected = [
        {"key": "far", "expected_eur": 1000.01, "since": "2026-03-01"},
        {"key": "near", "expected_eur": 1000.00, "since": "2026-03-01"},
    ]
    bank = [{"date": "2026-03-05", "amount": 1000.00, "description": "", "counterparty": ""}]
    res = BR.reconcile(bank, expected, eur_tol=0.05)
    assert len(res["matched"]) == 1
    assert res["matched"][0]["expected"]["key"] == "near"
    assert {e["key"] for e in res["unmatched_expected"]} == {"far"}
    assert res["unmatched_bank"] == []


def test_reconcile_reports_unmatched_both_sides():
    expected = [{"key": "owed", "expected_eur": 800.00, "since": "2026-03-01"}]
    bank = [{"date": "2026-03-05", "amount": 42.00, "description": "fee",
             "counterparty": "Bank"}]
    res = BR.reconcile(bank, expected)
    assert res["matched"] == []
    assert len(res["unmatched_expected"]) == 1
    assert len(res["unmatched_bank"]) == 1


def test_reconcile_ignores_debit_lines():
    # outgoing debits are never refund receipts and never appear in unmatched_bank
    expected = []
    bank = [{"date": "2026-03-05", "amount": -500.00, "description": "card",
             "counterparty": "Fuel co"}]
    res = BR.reconcile(bank, expected)
    assert res["unmatched_bank"] == []


def test_reconcile_never_raises_on_garbage():
    assert BR.reconcile(None, None)["matched"] == []
    assert BR.reconcile([{"amount": "x"}], [{"expected_eur": None, "since": None}]) \
        ["matched"] == []
    assert BR.reconcile("nonsense", "nonsense")["matched"] == []


# ----------------------------------------------------------------------------
# parse_bank_csv
# ----------------------------------------------------------------------------
def test_parse_bank_csv_aliases_and_bom():
    # utf-8-sig BOM + header aliases (Booking Date / Reference / Name)
    csv = ("﻿Booking Date,Amount,Reference,Name\n"
           "2026-03-10,1234.56,VAT refund Q1,Tax Authority\n"
           "2026-03-11,-50.00,Bank fee,Bank\n")
    lines = BR.parse_bank_csv(csv.encode("utf-8-sig"))
    assert len(lines) == 2
    assert lines[0] == {"date": "2026-03-10", "amount": 1234.56,
                        "description": "VAT refund Q1", "counterparty": "Tax Authority"}
    assert lines[1]["amount"] == -50.00


def test_parse_bank_csv_credit_debit_pair():
    csv = ("date,credit,debit,description\n"
           "2026-03-10,500.00,,refund\n"
           "2026-03-11,,80.00,fee\n")
    lines = BR.parse_bank_csv(csv.encode("utf-8"))
    assert lines[0]["amount"] == 500.00
    assert lines[1]["amount"] == -80.00


def test_parse_bank_csv_skips_malformed_row():
    csv = ("date,amount,description\n"
           "2026-03-10,100.00,good\n"
           "not-a-date,xx,bad\n"
           "2026-03-12,200.00,also good\n")
    lines = BR.parse_bank_csv(csv.encode("utf-8"))
    assert len(lines) == 2
    assert [l["amount"] for l in lines] == [100.00, 200.00]


def test_parse_bank_csv_never_raises_on_garbage():
    assert BR.parse_bank_csv(b"\xff\xfe\x00garbage") == []
    assert BR.parse_bank_csv(b"") == []
    assert BR.parse_bank_csv(None) == []
    assert BR.parse_bank_csv(b"no,headers,that,match\n1,2,3,4") == []


# ----------------------------------------------------------------------------
# expected_refunds — reuses recovery_report
# ----------------------------------------------------------------------------
def test_expected_refunds_reuses_recovery_report(monkeypatch):
    import vat_refund
    fake_out = [
        {"entity": "AAA", "country": "LT", "period": "2026-Q1", "vat_eur": 1000.0,
         "status": "submitted", "submitted": "2026-03-01"},
        {"entity": "BBB", "country": "PL", "period": "2026-Q1", "vat_eur": 2000.0,
         "status": "approved", "submitted": "2026-02-15"},
        {"entity": "CCC", "country": "DE", "period": "2026-Q1", "vat_eur": 500.0,
         "status": "paid", "submitted": "2026-01-10"},   # paid -> NOT expected
    ]
    monkeypatch.setattr(vat_refund, "recovery_report",
                        lambda year=None: (fake_out, {"outstanding": 3000.0}))
    rows = BR.expected_refunds("2026")
    assert {r["entity"] for r in rows} == {"AAA", "BBB"}   # paid excluded
    aaa = next(r for r in rows if r["entity"] == "AAA")
    assert aaa["expected_eur"] == 1000.0
    assert aaa["since"] == "2026-03-01"
    assert aaa["key"] == "AAA|LT|2026-Q1"


def test_expected_refunds_never_raises(monkeypatch):
    import vat_refund

    def boom(year=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(vat_refund, "recovery_report", boom)
    assert BR.expected_refunds() == []


# ----------------------------------------------------------------------------
# Provider seam
# ----------------------------------------------------------------------------
def test_null_provider_default_returns_empty():
    p = BR.provider()
    assert p.name == "none"
    assert p.fetch_transactions("acct") == []


def test_provider_unknown_name_falls_back(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: "nonsense_provider")
    assert isinstance(BR.provider(), BR.NullProvider)


def test_provider_setting_read_failure_falls_back(monkeypatch):
    import auth

    def boom(k, d=None):
        raise RuntimeError("settings down")

    monkeypatch.setattr(auth, "get_setting", boom)
    assert isinstance(BR.provider(), BR.NullProvider)


# ----------------------------------------------------------------------------
# ADVISORY proof — reconcile does not touch vat_applications
# ----------------------------------------------------------------------------
def test_reconcile_does_not_mutate_claims():
    import vat_refund
    con = vat_refund.connect()
    before = con.execute(
        "SELECT entity, refund_country, ref_period, status, status_code, vat_eur, "
        "paid_date, paid_amount FROM vat_applications ORDER BY 1,2,3").fetchall()
    before = [tuple(r) for r in before]
    con.close()

    expected = BR.expected_refunds("2026")
    # fabricate a bank line that would "match" the first expected, if any
    bank = []
    if expected:
        e = expected[0]
        bank = [{"date": e["since"], "amount": e["expected_eur"],
                 "description": "refund", "counterparty": "tax"}]
    BR.reconcile(bank, expected)

    con = vat_refund.connect()
    after = con.execute(
        "SELECT entity, refund_country, ref_period, status, status_code, vat_eur, "
        "paid_date, paid_amount FROM vat_applications ORDER BY 1,2,3").fetchall()
    after = [tuple(r) for r in after]
    con.close()
    assert before == after, "reconciliation must NOT change any claim row"


# ----------------------------------------------------------------------------
# Web — admin-only surface
# ----------------------------------------------------------------------------
def _csrf(client, path):
    body = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


def test_recon_page_admin_uploads_and_sees_tables(client, monkeypatch):
    import vat_refund
    fake_out = [
        {"entity": "AAA", "country": "LT", "period": "2026-Q1", "vat_eur": 1234.56,
         "status": "submitted", "submitted": "2026-03-01"},
        {"entity": "BBB", "country": "PL", "period": "2026-Q1", "vat_eur": 999.00,
         "status": "approved", "submitted": "2026-03-02"},
    ]
    monkeypatch.setattr(vat_refund, "recovery_report",
                        lambda year=None: (fake_out, {"outstanding": 2233.56}))

    page = client.get("/recon").get_data(as_text=True)
    assert "Advisory reconciliation" in page
    tok = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)

    csv = ("date,amount,description,counterparty\n"
           "2026-03-10,1234.56,VAT refund,Tax Authority\n"
           "2026-03-12,77.00,Random credit,Someone\n").encode("utf-8")
    from io import BytesIO
    r = client.post("/recon", data={
        "_csrf": tok, "__act": "upload",
        "file": (BytesIO(csv), "stmt.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Matched refunds" in html
    assert "1,234.56" in html                 # matched expected/bank amount
    assert "Outstanding" in html
    assert "Unmatched incoming credits" in html
    assert "Advisory reconciliation" in html


def test_recon_blocked_for_non_admin(admin_session):
    import app as A
    import auth
    auth.add_user("recon_proc", "Pw!23456", role="processor")
    c = A.app.test_client()
    c.post("/login", data={"username": "recon_proc", "password": "Pw!23456"})
    assert c.get("/recon").status_code == 403
