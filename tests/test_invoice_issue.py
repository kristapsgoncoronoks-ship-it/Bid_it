"""
Tests for invoice_issue.py (the customer service-fee invoice) and the web wiring:
build figures (net/vat/gross via money.f2), a sequential non-reused number, esc-safe
HTML, the /dokobit/postback route (signing_completed vaults + marks signed; a bad token
is ignored, not 500), and the admin set_dokobit settings round-trip (token never echoed).
"""
import re

import pytest

import auth
import invoice_issue as ii
import money


# ---------------------------------------------------------------- build figures
def test_build_invoice_figures_no_vat():
    auth.set_setting("invoice_fee_vat_pct", "0")
    inv = ii.build_invoice("ACME", "DE", "2026-Q1", recovered_vat_eur=10000,
                           fee_eur=1234.567, number="INV2026-0001")
    assert inv["totals"]["net"] == money.f2(1234.567)      # 1234.57
    assert inv["totals"]["vat"] == 0.0
    assert inv["totals"]["gross"] == money.f2(1234.567)
    assert inv["lines"][0]["net"] == money.f2(1234.567)


def test_build_invoice_figures_with_vat():
    auth.set_setting("invoice_fee_vat_pct", "21")
    inv = ii.build_invoice("ACME", "DE", "2026-Q1", 10000, 1000, number="INV2026-0002")
    assert inv["totals"]["net"] == 1000.0
    assert inv["totals"]["vat"] == money.f2(210)            # 21% of 1000
    assert inv["totals"]["gross"] == money.f2(1210)


# ---------------------------------------------------------------- sequential number
def test_sequential_non_reused_number():
    inv1 = ii.build_invoice("ACME", "DE", "2026-Q1", 100, 10)
    r1 = ii.record(inv1)
    inv2 = ii.build_invoice("ACME", "FR", "2026-Q1", 200, 20)
    r2 = ii.record(inv2)
    assert r1["number"] == "INV2026-0001"
    assert r2["number"] == "INV2026-0002"
    # never reused: a third draws the next sequence value
    r3 = ii.record(ii.build_invoice("ACME", "IT", "2026-Q1", 300, 30))
    assert r3["number"] == "INV2026-0003"
    # UNIQUE(number) is the hard backstop — listing reflects all three
    nums = {r["number"] for r in ii.list_invoices()}
    assert {"INV2026-0001", "INV2026-0002", "INV2026-0003"} <= nums


# ---------------------------------------------------------------- esc-safe HTML
def test_render_html_is_esc_safe():
    inv = ii.build_invoice('<script>alert(1)</script>', "DE", "2026-Q1", 100, 10,
                           number="INV2026-0009")
    html = ii.render_html(inv)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    # the number and a money figure render
    assert "INV2026-0009" in html


# ---------------------------------------------------------------- lifecycle helpers
def test_signing_and_signed_lifecycle():
    r = ii.record(ii.build_invoice("ACME", "DE", "2026-Q1", 100, 10))
    assert r["status"] == "draft"
    ii.set_signing(r["id"], "sign-tok-1", "https://x/sign", "sent")
    assert ii.by_signing_token("sign-tok-1")["status"] == "sent"
    assert ii.mark_signed("sign-tok-1", "lake://signed.pdf") is True
    row = ii.by_signing_token("sign-tok-1")
    assert row["status"] == "signed"
    assert row["signed_doc_ref"] == "lake://signed.pdf"
    # an unknown token is a no-op, never raises
    assert ii.mark_signed("nope", "x") is False


# ---------------------------------------------------------------- web: postback route
def _tok(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"', client.get(path).data.decode()).group(1)


def test_postback_signing_completed_vaults_and_marks_signed(client, monkeypatch):
    # Issue an invoice and attach a signing token we will "complete".
    r = ii.record(ii.build_invoice("ACME", "DE", "2026-Q1", 100, 10))
    ii.set_signing(r["id"], "sign-tok-web", "https://x/sign", "sent")

    import dokobit
    monkeypatch.setattr(dokobit, "enabled", lambda: True)
    monkeypatch.setattr(dokobit, "fetch_signed", lambda url: b"%PDF-signed")

    resp = client.post("/dokobit/postback", data={
        "action": "signing_completed", "token": "sign-tok-web",
        "status": "completed", "file": "https://gw/f/signed"})
    assert resp.status_code == 200
    row = ii.by_signing_token("sign-tok-web")
    assert row["status"] == "signed"
    assert row["signed_doc_ref"]            # vaulted locator stored


def test_postback_unknown_token_is_ignored_not_500(client):
    resp = client.post("/dokobit/postback", data={
        "action": "signing_completed", "token": "no-such-token",
        "status": "completed", "file": "https://gw/f/x"})
    assert resp.status_code == 200          # acknowledged, not an error


# ---------------------------------------------------------------- web: admin set_dokobit
def test_admin_set_dokobit_seals_token_and_never_echoes(client):
    tok = "dokobit-very-secret-XYZ"
    r = client.post("/admin", data={
        "_csrf": _tok(client, "/admin"), "__act": "set_dokobit",
        "dokobit_enabled": "on", "dokobit_env": "production",
        "dokobit_token": tok,
        "dokobit_postback_url": "https://us.example/dokobit/postback",
        "dokobit_return_url": "https://us.example/done",
        "invoice_fee_vat_pct": "21"})
    assert r.status_code == 200
    page = r.data.decode()
    assert tok not in page                  # token never echoed back
    assert auth.get_setting("dokobit_env", "") == "production"
    assert auth.get_setting("dokobit_postback_url", "").endswith("/dokobit/postback")
    assert auth.get_setting("invoice_fee_vat_pct", "") in ("21", "21.0")
    import dokobit
    assert dokobit.has_token() is True
    assert dokobit._token() == tok          # sealed round-trip
    # the at-rest value is the sealed blob, not the plaintext
    assert tok not in auth.get_setting("dokobit_token_sealed", "")
