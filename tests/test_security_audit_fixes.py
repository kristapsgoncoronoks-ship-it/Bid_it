"""
Regression tests for the five audited security findings:

  C1 — path traversal + untrusted-pickle RCE on /extract/confirm (token must be 16-hex)
  H1 — /api/recovery leaked admin-only financials to processors (now ADMIN_ONLY)
  H2 — XML billion-laughs DoS on untrusted invoice/price XML (defused parsing)
  M1 — Dokobit postback SSRF + token leak via body file_url (host allowlist + status-derived URL)
  M2 — fail-closed endpoint-coverage self-check (every route classified)
"""
import re

import pytest

import app as A


# --------------------------------------------------------------- helpers
def _processor_client():
    """A logged-in PROCESSOR (non-admin) test client."""
    import auth
    auth.add_user("sec_processor", "Pw!23456", role="processor")
    c = A.app.test_client()
    r = c.post("/login", data={"username": "sec_processor", "password": "Pw!23456"})
    assert r.status_code == 302, f"processor login failed: {r.status_code}"
    return c


def _csrf(client, path="/queue"):
    body = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


# =============================================================== C1
def test_c1_valid_extract_token_regex():
    assert A._valid_extract_token("0123456789abcdef") is True
    assert A._valid_extract_token("../../etc/passwd") is False
    assert A._valid_extract_token("abc") is False
    assert A._valid_extract_token("") is False
    assert A._valid_extract_token("0123456789ABCDEF") is False     # uppercase not minted
    assert A._valid_extract_token("0123456789abcdeff") is False    # 17 chars
    assert A._valid_extract_token(None) is False


@pytest.mark.parametrize("bad", ["../../etc/passwd", "abc", ""])
def test_c1_confirm_rejects_forged_token_no_file_access(client, bad, monkeypatch):
    # A forged token must hit the safe "session expired / not found" path and NEVER
    # reach os.path.join / pickle.load. We assert pickle.load is never called.
    import pickle
    called = {"n": 0}
    real = pickle.load
    monkeypatch.setattr(pickle, "load", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or real(*a, **k))
    tok = _csrf(client, "/extract")
    r = client.post("/extract/confirm", data={"token": bad, "_csrf": tok, "nlines": "0"})
    assert r.status_code == 200                               # not a 500, not a crash
    assert b"no longer available" in r.data
    assert called["n"] == 0, "forged token must never reach pickle.load"


def test_c1_load_draft_rejects_traversal(tmp_path, monkeypatch):
    # _load_draft must refuse a traversal token before any filesystem access.
    assert A._load_draft("../../etc/passwd") is None
    assert A._load_draft("abc") is None
    # a server-shaped token simply finds no file -> None (no exception)
    assert A._load_draft("0123456789abcdef") is None


def test_c1_valid_token_round_trips(monkeypatch):
    # A legitimate 16-hex token still stashes + loads a draft (the gate doesn't break the
    # happy path). Redirect the temp dir into a fresh location to avoid repo churn.
    tok = "0123456789abcdef"
    A._stash_draft(tok, {"supplier": "X", "lines": []})
    try:
        got = A._load_draft(tok)
        assert got == {"supplier": "X", "lines": []}
    finally:
        import os
        p = os.path.join(A.WORKDIR, ".extract_tmp", tok + ".draft.json")
        if os.path.exists(p):
            os.unlink(p)


# =============================================================== H1
def test_h1_api_recovery_is_admin_only():
    assert "api_recovery" in A.ADMIN_ONLY


def test_h1_api_recovery_forbidden_for_processor():
    c = _processor_client()
    r = c.get("/api/recovery?year=2026")
    assert r.status_code == 403


def test_h1_api_recovery_allowed_for_admin(client):
    r = client.get("/api/recovery?year=2026")
    assert r.status_code == 200
    assert r.is_json


# =============================================================== H2
_BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<Invoice>&lol3;</Invoice>"""


def test_h2_safexml_blocks_billion_laughs():
    import safexml
    # With defusedxml present this raises EntitiesForbidden (a parse error). It must NOT
    # expand the entity (which would hang / blow up memory).
    with pytest.raises(Exception):
        safexml.fromstring(_BILLION_LAUGHS)


def test_h2_safexml_parses_benign_xml():
    import safexml
    root = safexml.fromstring(b"<Invoice><ID>7</ID></Invoice>")
    assert root.tag == "Invoice"


def test_h2_extract_parse_einvoice_defused():
    # extract.parse_einvoice must reject the entity bomb (it routes through safexml).
    import extract
    with pytest.raises(Exception):
        extract.parse_einvoice(_BILLION_LAUGHS)


# =============================================================== M1
def test_m1_fetch_signed_refuses_off_allowlist_host(monkeypatch):
    import dokobit
    monkeypatch.setattr(dokobit, "enabled", lambda: True)
    monkeypatch.setattr(dokobit, "_token", lambda: "SECRET")

    hit = {"n": 0}

    class _Boom:
        def get(self, *a, **k):
            hit["n"] += 1
            raise AssertionError("must not fetch an off-allowlist host")

    import sys
    monkeypatch.setitem(sys.modules, "requests", _Boom())
    # an internal/foreign host must be refused BEFORE any requests.get
    assert dokobit.fetch_signed("http://169.254.169.254/latest/meta-data") is None
    assert dokobit.fetch_signed("https://evil.example.com/x.pdf") is None
    assert hit["n"] == 0


def test_m1_is_dokobit_host_allowlist():
    import dokobit
    base_host = "gateway-sandbox.dokobit.com"          # default env
    assert dokobit._is_dokobit_host(f"https://{base_host}/file/x.pdf") is True
    assert dokobit._is_dokobit_host("https://gateway.dokobit.com/file/x.pdf") is True
    assert dokobit._is_dokobit_host("https://evil.com/x.pdf") is False
    assert dokobit._is_dokobit_host("http://gateway-sandbox.dokobit.com/x") is False  # not https
    assert dokobit._is_dokobit_host("") is False


def test_m1_postback_uses_status_not_body_url(monkeypatch):
    # The postback must derive the download URL from signing_status, NEVER the body file_url.
    import app as A
    import dokobit as _dk
    import invoice_issue as _ii

    # a recognised signing token -> an issued invoice
    monkeypatch.setattr(_ii, "by_signing_token", lambda t: {"number": "INV1", "customer": "C"})
    monkeypatch.setattr(_ii, "mark_signed", lambda *a, **k: None)

    status_calls = {"n": 0}

    def _status(token):
        status_calls["n"] += 1
        return {"ok": True, "status": "completed",
                "signed_file_url": "https://gateway-sandbox.dokobit.com/file/good.pdf"}

    monkeypatch.setattr(_dk, "signing_status", _status)

    fetched = {"url": None}

    def _fetch(url):
        fetched["url"] = url
        return None                                    # no bytes needed for the assertion

    monkeypatch.setattr(_dk, "fetch_signed", _fetch)

    c = A.app.test_client()                            # public, no-cookie route
    r = c.post("/dokobit/postback", data={
        "status": "completed", "token": "sometoken",
        "file_url": "https://evil.example.com/exfil.pdf",   # attacker-controlled body URL
    })
    assert r.status_code == 200
    assert status_calls["n"] == 1, "must consult the authoritative status response"
    # the URL actually fetched is the STATUS-derived one, never the body file_url
    assert fetched["url"] == "https://gateway-sandbox.dokobit.com/file/good.pdf"


# =============================================================== M2
def test_m2_every_endpoint_is_classified():
    classified = (set(A.PERM_BY_ENDPOINT) | set(A.ADMIN_ONLY)
                  | set(A.API_V1_SCOPE) | set(A.OPEN_ENDPOINTS))
    unclassified = sorted(
        rule.endpoint for rule in A.app.url_map.iter_rules()
        if rule.endpoint != "static" and rule.endpoint not in classified)
    assert unclassified == [], f"unclassified endpoint(s): {unclassified}"


def test_m2_self_check_returns_empty_on_clean_app():
    assert A._assert_endpoint_coverage() == set()
