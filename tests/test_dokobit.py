"""
Tests for the OPTIONAL, default-OFF Dokobit signing seam (dokobit.py). No real network
is ever hit — `requests` is monkeypatched. Exercises the hard invariants: default-off,
no network call when OFF, sealed token never leaks, the create_signing URL/body shape,
transport-error -> error dict, and postback parsing.
"""
import base64
import json

import pytest

import auth
import dokobit


# ---------------------------------------------------------------- fake requests
class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = (payload if isinstance(payload, (bytes, bytearray))
                        else json.dumps(payload).encode())

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeRequests:
    """Records every call so a test can assert URL / params / body, and asserts NO call
    happens when the seam is OFF."""
    def __init__(self):
        self.calls = []
        self.post_payload = {}
        self.get_payload = {}
        self.raise_transport = False

    def post(self, url, params=None, json=None, data=None, timeout=None):
        self.calls.append(("POST", url, params, json, data))
        if self.raise_transport:
            raise RuntimeError("connection refused")
        return _Resp(self.post_payload)

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, params, None, None))
        if self.raise_transport:
            raise RuntimeError("connection refused")
        return _Resp(self.get_payload)


@pytest.fixture
def fake_requests(monkeypatch):
    fr = _FakeRequests()
    import sys
    monkeypatch.setitem(sys.modules, "requests", fr)
    return fr


def _configure(token="tok-secret-123", env="sandbox"):
    auth.set_setting("dokobit_enabled", "on")
    auth.set_setting("dokobit_env", env)
    dokobit.set_token(token)


# ---------------------------------------------------------------- enabled()
def test_enabled_default_false():
    assert dokobit.enabled() is False


def test_enabled_requires_switch_and_token(fake_requests):
    auth.set_setting("dokobit_enabled", "on")
    assert dokobit.enabled() is False          # no token yet
    dokobit.set_token("abc")
    assert dokobit.enabled() is True
    auth.set_setting("dokobit_enabled", "off")
    assert dokobit.enabled() is False           # switch off => inert


# ---------------------------------------------------------------- OFF => no network
def test_off_makes_no_network_call(fake_requests):
    # Seam OFF (default): every method returns not-configured and calls NOTHING.
    assert dokobit.upload_file(b"x", "a.pdf") == {"ok": False,
                                                  "error": "Dokobit not configured"}
    assert dokobit.create_signing([{"token": "t", "filename": "a"}],
                                  [{"name": "A", "surname": "B"}])["ok"] is False
    assert dokobit.signing_status("s")["ok"] is False
    assert dokobit.fetch_signed("http://x") is None
    assert fake_requests.calls == []            # NO HTTP at all


# ---------------------------------------------------------------- env / base
def test_base_url_by_env():
    auth.set_setting("dokobit_env", "sandbox")
    assert dokobit._base() == "https://gateway-sandbox.dokobit.com"
    auth.set_setting("dokobit_env", "production")
    assert dokobit._base() == "https://gateway.dokobit.com"
    auth.set_setting("dokobit_env", "garbage")
    assert dokobit._base() == "https://gateway-sandbox.dokobit.com"   # safe fallback


# ---------------------------------------------------------------- sealed token
def test_token_seal_roundtrip_and_never_leaks(fake_requests):
    assert dokobit.has_token() is False
    dokobit.set_token("super-secret-token")
    assert dokobit.has_token() is True
    auth.set_setting("dokobit_enabled", "on")
    cfg = dokobit.config()
    assert cfg["has_token"] is True
    assert "super-secret-token" not in str(cfg)       # never echoed
    assert dokobit._token() == "super-secret-token"    # private decrypt round-trips
    # The stored value at rest is the sealed blob, not the plaintext.
    raw = auth.get_setting("dokobit_token_sealed", "")
    assert "super-secret-token" not in raw
    assert base64.b64decode(raw).startswith(b"FFSv1")
    dokobit.set_token("")
    assert dokobit.has_token() is False


# ---------------------------------------------------------------- upload
def test_upload_file_builds_call(fake_requests):
    _configure()
    fake_requests.post_payload = {"status": "ok", "token": "file-tok-1"}
    out = dokobit.upload_file(b"hello-bytes", "inv.pdf")
    assert out == {"ok": True, "token": "file-tok-1"}
    method, url, params, jbody, data = fake_requests.calls[-1]
    assert method == "POST"
    assert url == "https://gateway-sandbox.dokobit.com/api/file/upload.json"
    assert params == {"access_token": "tok-secret-123"}
    # base64 content, never the raw token in the body
    assert data["file[content]"] == base64.b64encode(b"hello-bytes").decode()
    assert data["file[name]"] == "inv.pdf"


# ---------------------------------------------------------------- create_signing
def test_create_signing_builds_url_and_body(fake_requests):
    _configure()
    auth.set_setting("dokobit_postback_url", "https://us.example/dokobit/postback")
    auth.set_setting("dokobit_return_url", "https://us.example/done")
    fake_requests.post_payload = {
        "status": "ok", "token": "signing-tok-9",
        "signers": [{"id": "S1", "access_token": "signer-at-1"}],
    }
    files = [{"token": "file-tok-1", "filename": "inv.pdf"}]
    signers = [{"name": "Jane", "surname": "Doe", "code": "39001010000",
                "country": "lt", "email": "jane@example.com"}]
    out = dokobit.create_signing(files, signers)
    assert out["ok"] is True
    assert out["signing_token"] == "signing-tok-9"
    su = out["signers"][0]["sign_url"]
    assert su == ("https://gateway-sandbox.dokobit.com/signing/signing-tok-9"
                  "?access_token=signer-at-1")
    method, url, params, jbody, data = fake_requests.calls[-1]
    assert url == "https://gateway-sandbox.dokobit.com/api/signing/create.json"
    assert params == {"access_token": "tok-secret-123"}     # token in query, not body
    assert jbody["type"] == "pdf"                            # QES/PAdES type
    assert jbody["files"] == [{"token": "file-tok-1", "filename": "inv.pdf"}]
    assert jbody["signers"][0]["email"] == "jane@example.com"
    assert jbody["postback_url"] == "https://us.example/dokobit/postback"
    assert jbody["return_url"] == "https://us.example/done"


def test_create_signing_transport_error_maps_to_error(fake_requests):
    _configure()
    fake_requests.raise_transport = True
    out = dokobit.create_signing([{"token": "f", "filename": "a"}],
                                 [{"name": "A", "surname": "B"}])
    assert out["ok"] is False
    assert "create signing failed" in out["error"]


# ---------------------------------------------------------------- status / fetch
def test_signing_status(fake_requests):
    _configure()
    fake_requests.get_payload = {"status": "completed",
                                 "file": "https://gateway-sandbox.dokobit.com/f/x"}
    out = dokobit.signing_status("signing-tok-9")
    assert out == {"ok": True, "status": "completed",
                   "signed_file_url": "https://gateway-sandbox.dokobit.com/f/x"}
    assert fake_requests.calls[-1][1].endswith("/api/signing/signing-tok-9/status.json")


def test_fetch_signed_returns_bytes(fake_requests):
    _configure()
    fake_requests.get_payload = b"%PDF-signed-bytes"
    data = dokobit.fetch_signed("https://gateway-sandbox.dokobit.com/f/x")
    assert data == b"%PDF-signed-bytes"
    # token passed as a param, never logged/embedded in the URL string
    assert fake_requests.calls[-1][2] == {"access_token": "tok-secret-123"}


# ---------------------------------------------------------------- postback parse
def test_parse_postback():
    pb = dokobit.parse_postback({"action": "signing_completed",
                                 "token": "signing-tok-9",
                                 "status": "completed",
                                 "file": "https://x/f"})
    assert pb == {"action": "signing_completed", "signing_token": "signing-tok-9",
                  "status": "completed", "file_url": "https://x/f"}
    # defensive: a junk payload never raises
    assert dokobit.parse_postback(None)["signing_token"] == ""


# ---------------------------------------------------------------- eID stub
def test_authenticate_is_stub():
    assert dokobit.authenticate()["ok"] is False
