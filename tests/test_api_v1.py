"""
Security + contract tests for the versioned, token-only /api/v1 API.

Covers: a scoped key reaches its endpoint (200 JSON); missing/invalid/revoked
token -> 401; valid-but-out-of-scope -> 403; no IBAN/secret/PII leaks in any v1
payload; usage is metered + last_used bumped; the OLD session-authed /api/* and
non-api routes are unaffected by the token path.

Keys are issued against the throwaway test security.db (conftest restores it),
so nothing real is clobbered.
"""
import re
import pytest
import app as A
import api_keys


def _client():
    """A fresh, NOT-logged-in client (no session cookie)."""
    return A.app.test_client()


def _issue(scopes, label="t"):
    kid, token = api_keys.issue(label, scopes, "pytest_admin")
    return kid, token


# ---------------------------------------------------------------- happy path

def test_scoped_key_reaches_endpoint(admin_session):
    _, token = _issue(["api:benchmark"])
    r = _client().get("/api/v1/benchmark", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.is_json
    body = r.get_json()
    assert "rows" in body and "basis" in body


def test_x_api_key_header_also_works(admin_session):
    _, token = _issue(["api:savings"])
    r = _client().get("/api/v1/savings", headers={"X-API-Key": token})
    assert r.status_code == 200 and r.is_json


def test_all_three_v1_endpoints_with_scopes(admin_session):
    for path, scope in (("/api/v1/benchmark", "api:benchmark"),
                        ("/api/v1/claim-status", "api:claims"),
                        ("/api/v1/savings", "api:savings")):
        _, token = _issue([scope])
        r = _client().get(path, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200 and r.is_json, path


# ---------------------------------------------------------------- auth failures

def test_missing_token_is_401(admin_session):
    r = _client().get("/api/v1/benchmark")
    assert r.status_code == 401 and r.is_json
    assert "error" in r.get_json()


def test_invalid_token_is_401(admin_session):
    r = _client().get("/api/v1/benchmark",
                      headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_out_of_scope_is_403(admin_session):
    # a key with only api:benchmark must NOT reach the claims endpoint
    _, token = _issue(["api:benchmark"])
    r = _client().get("/api/v1/claim-status",
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_revoked_key_is_401(admin_session):
    kid, token = _issue(["api:savings"])
    # works before revoke
    assert _client().get("/api/v1/savings",
                         headers={"Authorization": f"Bearer {token}"}).status_code == 200
    api_keys.revoke(kid)
    # fails immediately after revoke
    assert _client().get("/api/v1/savings",
                         headers={"Authorization": f"Bearer {token}"}).status_code == 401


# ---------------------------------------------------------------- no PII / secrets

_FORBIDDEN_KEYS = {"iban", "payout_to", "fee_eur", "fee_invoice_no", "secret",
                   "token", "password", "home", "bank"}


def _assert_no_pii(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert k.lower() not in _FORBIDDEN_KEYS, f"leaked field {k}"
            _assert_no_pii(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_no_pii(v)


def test_claim_status_has_no_sensitive_fields(admin_session):
    _, token = _issue(["api:claims"])
    body = _client().get("/api/v1/claim-status",
                         headers={"Authorization": f"Bearer {token}"}).get_json()
    _assert_no_pii(body)
    # positively confirm only the whitelisted fields are present
    allowed = set(A._V1_CLAIM_FIELDS)
    for row in body["claims"]:
        assert set(row.keys()) <= allowed


def test_all_v1_payloads_have_no_pii(admin_session):
    for path, scope in (("/api/v1/benchmark", "api:benchmark"),
                        ("/api/v1/claim-status", "api:claims"),
                        ("/api/v1/savings", "api:savings")):
        _, token = _issue([scope])
        body = _client().get(path, headers={"Authorization": f"Bearer {token}"}).get_json()
        _assert_no_pii(body)


# ---------------------------------------------------------------- metering

def test_usage_is_logged_and_last_used_bumped(admin_session):
    kid, token = _issue(["api:benchmark"])
    before = [k for k in api_keys.list_keys() if k["id"] == kid][0]
    assert before["last_used"] is None
    n0 = api_keys.usage_summary().get(kid, {}).get("calls", 0)
    _client().get("/api/v1/benchmark", headers={"Authorization": f"Bearer {token}"})
    after = [k for k in api_keys.list_keys() if k["id"] == kid][0]
    assert after["last_used"] is not None, "last_used not bumped"
    n1 = api_keys.usage_summary().get(kid, {}).get("calls", 0)
    assert n1 == n0 + 1, "call not metered"


def test_unauthorized_calls_are_metered_too(admin_session):
    kid, token = _issue(["api:benchmark"])
    n0 = api_keys.usage_summary().get(kid, {}).get("calls", 0)
    # out-of-scope (403) is metered under the key id
    _client().get("/api/v1/claim-status",
                  headers={"Authorization": f"Bearer {token}"})
    n1 = api_keys.usage_summary().get(kid, {}).get("calls", 0)
    assert n1 == n0 + 1


# ---------------------------------------------------------------- hashing discipline

def test_token_is_not_stored_in_plaintext(admin_session):
    _, token = _issue(["api:savings"])
    con = api_keys.connect()
    rows = con.execute("SELECT token_sha256 FROM api_keys").fetchall()
    con.close()
    blob = api_keys._hash(token)
    # the stored value is the SHA-256 hash (bytes), and the plaintext appears nowhere
    stored = [r[0] for r in rows]
    assert blob in stored
    assert token.encode() not in stored


def test_constant_time_compare_used():
    import inspect
    src = inspect.getsource(api_keys.verify)
    assert "compare_digest" in src


# ---------------------------------------------------------------- isolation

def test_session_api_still_session_authed(client, admin_session):
    # logged-in client (the `client` fixture) reaches the OLD /api/* fine
    assert client.get("/api/periods").status_code == 200
    # a NON-logged-in client is redirected to /login (token path did NOT change this)
    r = _client().get("/api/periods")
    assert r.status_code == 302 and "/login" in r.headers.get("Location", "")


def test_v1_token_does_not_authenticate_other_routes(admin_session):
    # a perfectly valid v1 token must NOT grant access to a session-only route
    _, token = _issue(["api:benchmark", "api:claims", "api:savings"])
    r = _client().get("/api/periods", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 302 and "/login" in r.headers.get("Location", "")
    # and a regular HTML page is likewise untouched
    r2 = _client().get("/", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code in (302, 301)


def test_non_api_route_unaffected(client):
    # a normal page renders for a logged-in session regardless of the token machinery
    assert client.get("/admin").status_code == 200
