"""
Tests for the CLOUDFLARE edge-safety package (cloudflare.py) and its app.py wiring.

Covers:
  * is_cloudflare_ip membership (a known CF range IP True; 8.8.8.8 / a private IP False).
  * client_ip returns CF-Connecting-IP ONLY when trust is ON *and* the peer is a CF IP;
    a SPOOFED header from a non-CF peer is ignored; with trust OFF (default) client_ip
    is byte-identical to remote_addr.
  * peer_allowed True for CF / loopback, False for a random public IP.
  * the origin-lock before_request: 403 for a non-CF peer when ON, pass-through when OFF
    (default), and ALWAYS allows loopback.
  * DEFAULT-OFF proof: with neither setting set, _client_ip() == request.remote_addr and
    no request is blocked.

Settings are flipped via monkeypatch on cloudflare._read (the single setting reader), so a
test never touches security.db and never leaks state.
"""
import os
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import cloudflare

# A real address inside the first embedded Cloudflare IPv4 range (173.245.48.0/20).
CF_IP = "173.245.49.1"
CF_IP6 = "2400:cb00::1"          # inside 2400:cb00::/32
NON_CF_IP = "8.8.8.8"            # Google DNS — never a Cloudflare range
PRIVATE_IP = "10.1.2.3"
LOOPBACK = "127.0.0.1"


def _settings(monkeypatch, **vals):
    """Override cloudflare._read so the module reads the given settings (default OFF/blank)."""
    def fake_read(setting, default="off"):
        return vals.get(setting, default)
    monkeypatch.setattr(cloudflare, "_read", fake_read)


# ─────────────────────────────────────────────── is_cloudflare_ip
def test_is_cloudflare_ip_true_for_known_cf_range():
    assert cloudflare.is_cloudflare_ip(CF_IP) is True
    assert cloudflare.is_cloudflare_ip(CF_IP6) is True


def test_is_cloudflare_ip_false_for_public_and_private():
    assert cloudflare.is_cloudflare_ip(NON_CF_IP) is False
    assert cloudflare.is_cloudflare_ip(PRIVATE_IP) is False
    assert cloudflare.is_cloudflare_ip(LOOPBACK) is False


def test_is_cloudflare_ip_never_raises_on_garbage():
    assert cloudflare.is_cloudflare_ip("not-an-ip") is False
    assert cloudflare.is_cloudflare_ip("") is False
    assert cloudflare.is_cloudflare_ip(None) is False


def test_admin_override_replaces_builtin_ranges(monkeypatch):
    # When cloudflare_ip_ranges is set it REPLACES the embedded list.
    _settings(monkeypatch, cloudflare_ip_ranges="203.0.113.0/24")
    assert cloudflare.is_cloudflare_ip("203.0.113.5") is True
    # ...and a previously-CF IP is no longer recognised under the override.
    assert cloudflare.is_cloudflare_ip(CF_IP) is False


# ─────────────────────────────────────────────── client_ip
def test_client_ip_trust_off_is_byte_identical(monkeypatch):
    # Default OFF: client_ip must equal remote_addr regardless of the header.
    _settings(monkeypatch)  # everything off/blank
    assert cloudflare.client_ip(CF_IP, "1.2.3.4") == CF_IP
    assert cloudflare.client_ip(NON_CF_IP, "1.2.3.4") == NON_CF_IP
    assert cloudflare.client_ip(LOOPBACK, "9.9.9.9") == LOOPBACK


def test_client_ip_trusts_header_only_from_cf_peer(monkeypatch):
    _settings(monkeypatch, trust_cloudflare="on")
    # Peer IS Cloudflare -> the real visitor from the header is returned.
    assert cloudflare.client_ip(CF_IP, "203.0.113.7") == "203.0.113.7"


def test_client_ip_ignores_spoofed_header_from_non_cf_peer(monkeypatch):
    _settings(monkeypatch, trust_cloudflare="on")
    # Peer is NOT Cloudflare -> the (forged) header is ignored, remote_addr kept.
    assert cloudflare.client_ip(NON_CF_IP, "203.0.113.7") == NON_CF_IP


def test_client_ip_trusts_loopback_peer(monkeypatch):
    # Loopback is always a trusted local hop (e.g. nginx on the same box).
    _settings(monkeypatch, trust_cloudflare="on")
    assert cloudflare.client_ip(LOOPBACK, "203.0.113.7") == "203.0.113.7"


def test_client_ip_trusts_configured_proxy(monkeypatch):
    _settings(monkeypatch, trust_cloudflare="on",
              cloudflare_trusted_proxies="10.0.0.0/8")
    assert cloudflare.client_ip("10.5.6.7", "203.0.113.7") == "203.0.113.7"
    # an unlisted private peer is still not trusted
    assert cloudflare.client_ip("192.168.1.1", "203.0.113.7") == "192.168.1.1"


def test_client_ip_bad_header_falls_back(monkeypatch):
    _settings(monkeypatch, trust_cloudflare="on")
    assert cloudflare.client_ip(CF_IP, "garbage") == CF_IP
    assert cloudflare.client_ip(CF_IP, "") == CF_IP


# ─────────────────────────────────────────────── peer_allowed
def test_peer_allowed_for_cf_and_loopback():
    assert cloudflare.peer_allowed(CF_IP) is True
    assert cloudflare.peer_allowed(CF_IP6) is True
    assert cloudflare.peer_allowed(LOOPBACK) is True
    assert cloudflare.peer_allowed("::1") is True


def test_peer_allowed_false_for_random_public_ip():
    assert cloudflare.peer_allowed(NON_CF_IP) is False
    assert cloudflare.peer_allowed(PRIVATE_IP) is False


def test_peer_allowed_includes_configured_proxy(monkeypatch):
    _settings(monkeypatch, cloudflare_trusted_proxies="10.0.0.0/8")
    assert cloudflare.peer_allowed("10.9.9.9") is True


# ─────────────────────────────────────────────── enabled flags default OFF
def test_settings_default_off():
    # No monkeypatch — reads the real (clean, per-test isolated) app_settings.
    assert cloudflare.trust_enabled() is False
    assert cloudflare.origin_lock_enabled() is False


# ─────────────────────────────────────────────── origin-lock before_request (web)
def _raw_get(path, remote):
    """A request to the app with a chosen socket peer (REMOTE_ADDR), no session."""
    import app as A
    return A.app.test_client().get(path, environ_overrides={"REMOTE_ADDR": remote})


def test_origin_lock_off_passes_non_cf_peer(client):
    # Default OFF: a non-CF peer reaches the app normally (login redirect / page, not 403).
    r = _raw_get("/login", NON_CF_IP)
    assert r.status_code != 403


def test_origin_lock_on_blocks_non_cf_peer(client):
    import auth
    auth.set_setting(cloudflare.ORIGIN_SETTING, "on")
    r = _raw_get("/login", NON_CF_IP)
    assert r.status_code == 403


def test_origin_lock_on_allows_cf_peer(client):
    import auth
    auth.set_setting(cloudflare.ORIGIN_SETTING, "on")
    r = _raw_get("/login", CF_IP)
    assert r.status_code != 403


def test_origin_lock_on_always_allows_loopback(client):
    import auth
    auth.set_setting(cloudflare.ORIGIN_SETTING, "on")
    r = _raw_get("/login", LOOPBACK)
    assert r.status_code != 403


# ─────────────────────────────────────────────── _client_ip() default-OFF proof (web)
def test_app_client_ip_default_off_equals_remote_addr(client):
    """With neither setting set, app._client_ip() == request.remote_addr (byte-identical)
    and the request is not blocked — proves the package is inert by default."""
    import app as A
    with A.app.test_request_context(
            "/login", environ_overrides={"REMOTE_ADDR": CF_IP},
            headers={"CF-Connecting-IP": "203.0.113.7"}):
        from flask import request
        assert A._client_ip() == request.remote_addr == CF_IP


def test_app_client_ip_resolves_when_trust_on(client):
    import app as A
    import auth
    auth.set_setting(cloudflare.TRUST_SETTING, "on")
    with A.app.test_request_context(
            "/login", environ_overrides={"REMOTE_ADDR": CF_IP},
            headers={"CF-Connecting-IP": "203.0.113.7"}):
        assert A._client_ip() == "203.0.113.7"


def test_app_client_ip_ignores_spoof_when_trust_on(client):
    import app as A
    import auth
    auth.set_setting(cloudflare.TRUST_SETTING, "on")
    with A.app.test_request_context(
            "/login", environ_overrides={"REMOTE_ADDR": NON_CF_IP},
            headers={"CF-Connecting-IP": "203.0.113.7"}):
        # peer is not CF -> the forged header is ignored
        assert A._client_ip() == NON_CF_IP
