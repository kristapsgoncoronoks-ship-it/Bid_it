"""Open-banking SANDBOX provider (bank_recon.py) — the pluggable AISP provider exercised
end-to-end WITHOUT a live aggregator.

Asserts:
  * the BankFeedProvider protocol: fetch_transactions(account, since) -> normalized lines;
  * SandboxBankProvider returns DETERMINISTIC mock lines that INCLUDE credits matching the
    expected refunds (so reconcile produces hits) plus non-matching NOISE;
  * bank_provider=sandbox is selected by setting; the default (null) yields no txns;
  * the advisory recon over the sandbox feed matches the expected refunds;
  * NO VAT mutation — reconciliation over the sandbox feed never touches a claim.
"""
import os
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import bank_recon as BR


_FAKE_OUT = [
    {"entity": "AAA", "country": "LT", "period": "2026-Q1", "vat_eur": 1234.56,
     "status": "submitted", "submitted": "2026-03-01"},
    {"entity": "BBB", "country": "PL", "period": "2026-Q1", "vat_eur": 999.00,
     "status": "approved", "submitted": "2026-03-02"},
    {"entity": "CCC", "country": "DE", "period": "2026-Q1", "vat_eur": 500.0,
     "status": "paid", "submitted": "2026-01-10"},   # paid -> not expected
]


def _patch_recovery(monkeypatch):
    import vat_refund
    monkeypatch.setattr(vat_refund, "recovery_report",
                        lambda year=None: (_FAKE_OUT, {"outstanding": 2233.56}))


# ------------------------------------------------------------- provider seam

def test_sandbox_provider_selected_by_setting(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "get_setting",
                        lambda k, d=None: "sandbox" if k == "bank_provider" else d)
    p = BR.provider()
    assert isinstance(p, BR.SandboxBankProvider)
    assert p.name == "sandbox"


def test_default_provider_null_yields_no_txns(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: d)
    p = BR.provider()
    assert isinstance(p, BR.NullProvider)
    assert p.fetch_transactions("acct") == []


# --------------------------------------------------- sandbox deterministic feed

def test_sandbox_feed_includes_matches_and_noise(monkeypatch):
    _patch_recovery(monkeypatch)
    p = BR.SandboxBankProvider()
    lines = p.fetch_transactions("acct")
    # deterministic & repeatable
    assert lines == p.fetch_transactions("acct")
    # one credit per expected (2 open) + 1 noise credit + 1 noise debit
    amounts = sorted(round(l["amount"], 2) for l in lines)
    assert 1234.56 in amounts and 999.00 in amounts      # the matching credits
    assert 42.00 in amounts                              # non-matching noise credit
    assert -17.50 in amounts                             # noise debit (never a refund)
    # the matching credits land within the reconcile day-tolerance of `since`
    refund = next(l for l in lines if round(l["amount"], 2) == 1234.56)
    assert refund["date"] == "2026-03-06"               # 2026-03-01 + 5 days
    assert "sandbox" in refund["counterparty"].lower()


def test_sandbox_feed_reconciles_expected_refunds(monkeypatch):
    _patch_recovery(monkeypatch)
    p = BR.SandboxBankProvider()
    lines = p.fetch_transactions("acct")
    expected = BR.expected_refunds("2026")
    res = BR.reconcile(lines, expected)
    matched_keys = {m["expected"]["key"] for m in res["matched"]}
    assert matched_keys == {"AAA|LT|2026-Q1", "BBB|PL|2026-Q1"}
    assert res["unmatched_expected"] == []
    # the noise credit (42.00) is the only unmatched incoming credit; the debit is ignored
    assert [round(b["amount"], 2) for b in res["unmatched_bank"]] == [42.00]


def test_sandbox_feed_never_raises_on_broken_recovery(monkeypatch):
    import vat_refund

    def boom(year=None):
        raise RuntimeError("recovery down")

    monkeypatch.setattr(vat_refund, "recovery_report", boom)
    # still returns the noise lines, never raises
    lines = BR.SandboxBankProvider().fetch_transactions("acct")
    assert [round(l["amount"], 2) for l in lines] == [42.00, -17.50]


# ----------------------------------------------- ADVISORY proof: no VAT mutation

def test_sandbox_recon_does_not_mutate_claims():
    import vat_refund
    con = vat_refund.connect()
    before = [tuple(r) for r in con.execute(
        "SELECT entity, refund_country, ref_period, status, status_code, vat_eur, "
        "paid_date, paid_amount FROM vat_applications ORDER BY 1,2,3").fetchall()]
    con.close()

    p = BR.SandboxBankProvider()
    lines = p.fetch_transactions("acct")
    BR.reconcile(lines, BR.expected_refunds("2026"))

    con = vat_refund.connect()
    after = [tuple(r) for r in con.execute(
        "SELECT entity, refund_country, ref_period, status, status_code, vat_eur, "
        "paid_date, paid_amount FROM vat_applications ORDER BY 1,2,3").fetchall()]
    con.close()
    assert before == after, "sandbox reconciliation must NOT change any claim row"


# ----------------------------------------------------------------- web surface

def test_recon_page_sandbox_provider_selector(client):
    page = client.get("/recon").get_data(as_text=True)
    assert "Bank feed provider" in page
    assert "sandbox" in page
