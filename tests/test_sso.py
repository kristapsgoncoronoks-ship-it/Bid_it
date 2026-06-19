"""
Tests for the optional SSO (OIDC) connector — no real network is ever hit
(sso._discovery / exchange_code / userinfo are monkeypatched). Exercises the hard
invariants: default-off, sealed secret never leaks, domain allowlist, password-login
refusal for SSO users, the state/replay gate on the callback, and auto-provisioning.
"""
import os

import pytest

import auth
import sso


# ---------------------------------------------------------------- enabled()
def test_enabled_default_false():
    # Clean slate (conftest clears sso_* before each test).
    assert sso.enabled() is False


def test_enabled_true_once_fully_configured():
    auth.set_setting("sso_enabled", "on")
    auth.set_setting("sso_google_enabled", "on")
    auth.set_setting("sso_google_issuer", "https://accounts.google.com")
    auth.set_setting("sso_google_client_id", "client-123")
    # Still false without a secret.
    assert sso.enabled() is False
    sso.set_secret("google", "super-secret-value")
    assert sso.enabled() is True
    # Flipping the master switch off makes it inert again.
    auth.set_setting("sso_enabled", "off")
    assert sso.enabled() is False


# ---------------------------------------------------------------- domain allowlist
def test_domain_allowed_empty_allows_all():
    auth.set_setting("sso_allowed_domains", "")
    assert sso.domain_allowed("anyone@whatever.com") is True


def test_domain_allowed_with_allowlist():
    auth.set_setting("sso_allowed_domains", "Example.com, sub.example.com")
    assert sso.domain_allowed("user@example.com") is True       # case-insensitive
    assert sso.domain_allowed("user@SUB.EXAMPLE.COM") is True
    assert sso.domain_allowed("user@evil.com") is False
    assert sso.domain_allowed("not-an-email") is False


# ---------------------------------------------------------------- sealed secret
def test_secret_seal_open_roundtrip_and_never_leaks():
    assert sso.has_secret("google") is False
    sso.set_secret("google", "oidc-client-secret-xyz")
    assert sso.has_secret("google") is True
    # config() must NOT contain the plaintext anywhere.
    cfg = sso.config()
    assert cfg["providers"]["google"]["has_secret"] is True
    assert "oidc-client-secret-xyz" not in str(cfg)
    # The private decrypt path round-trips the exact value (used only for token exchange).
    assert sso._client_secret("google") == "oidc-client-secret-xyz"
    # Clearing removes it.
    sso.set_secret("google", "")
    assert sso.has_secret("google") is False


# ---------------------------------------------------------------- auth user model
def test_sso_user_cannot_password_login():
    email = "ssotest@example.com"
    # Clean any leftover (this test owns this username deterministically).
    con = auth.connect(); con.execute("DELETE FROM users WHERE username=?", (email,))
    con.commit(); con.close()
    auth.add_sso_user(email, email)
    u = auth.get_user_by_email(email)
    assert u is not None
    assert u["username"] == email
    assert u["role"] == "processor"
    assert (u["auth_source"] or "").lower() == "sso"
    # No password can ever authenticate an SSO user.
    assert auth.verify(email, "") is False
    assert auth.verify(email, "anything") is False
    assert auth.verify(email, "ssotest@example.com") is False
    # cleanup
    con = auth.connect(); con.execute("DELETE FROM users WHERE username=?", (email,))
    con.commit(); con.close()


def test_get_user_by_email_case_insensitive():
    email = "MixedCase@Example.com"
    con = auth.connect(); con.execute("DELETE FROM users WHERE LOWER(email)=LOWER(?)", (email,))
    con.commit(); con.close()
    auth.add_sso_user(email.lower(), email.lower())
    assert auth.get_user_by_email("mixedcase@example.com") is not None
    assert auth.get_user_by_email("MIXEDCASE@EXAMPLE.COM") is not None
    assert auth.get_user_by_email("missing@example.com") is None
    con = auth.connect(); con.execute("DELETE FROM users WHERE LOWER(email)=LOWER(?)", (email,))
    con.commit(); con.close()


# ---------------------------------------------------------------- helpers
def _configure_sso():
    # Configure the Google provider (the single-button scenarios reuse this).
    auth.set_setting("sso_enabled", "on")
    auth.set_setting("sso_google_enabled", "on")
    auth.set_setting("sso_google_issuer", "https://accounts.google.com")
    auth.set_setting("sso_google_client_id", "client-123")
    auth.set_setting("sso_auto_provision", "on")
    sso.set_secret("google", "the-secret")


def _patch_discovery(monkeypatch):
    monkeypatch.setattr(sso, "_discovery", lambda p=None, issuer=None: {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
    })


# ---------------------------------------------------------------- /sso/login route
def test_sso_login_redirects_to_login_when_disabled(client):
    # SSO not configured -> /sso/login is a no-op redirect to /login.
    r = client.get("/sso/login")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/login")


def test_sso_login_redirects_to_provider_and_stores_state(client, monkeypatch):
    _configure_sso()
    _patch_discovery(monkeypatch)
    r = client.get("/sso/login?provider=google")
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "response_type=code" in loc
    assert "state=" in loc and "nonce=" in loc
    assert "prompt=select_account" in loc
    with client.session_transaction() as s:
        assert s.get("sso_state")
        assert s.get("sso_nonce")
        assert s.get("sso_pending_provider") == "google"


# ---------------------------------------------------------------- /sso/callback route
def test_sso_callback_state_mismatch_refused(client, monkeypatch):
    _configure_sso()
    _patch_discovery(monkeypatch)
    with client.session_transaction() as s:
        s["sso_state"] = "the-correct-state"
        s["sso_pending_provider"] = "google"
        s.pop("user", None)
    r = client.get("/sso/callback?state=WRONG&code=abc")
    assert r.status_code == 200
    assert "state mismatch" in r.get_data(as_text=True)
    with client.session_transaction() as s:
        assert not s.get("user")   # no login happened


def test_sso_callback_happy_path_autoprovisions(client, monkeypatch):
    _configure_sso()
    auth.set_setting("sso_allowed_domains", "example.com")
    _patch_discovery(monkeypatch)
    email = "newhire@example.com"
    con = auth.connect(); con.execute("DELETE FROM users WHERE username=?", (email,))
    con.commit(); con.close()
    monkeypatch.setattr(sso, "resolve_identity",
                        lambda p, code, ru: {"ok": True, "email": email, "name": "New Hire",
                                             "reason": ""})
    with client.session_transaction() as s:
        s["sso_state"] = "match-me"
        s["sso_pending_provider"] = "google"
    r = client.get("/sso/callback?state=match-me&code=goodcode")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/")
    # The user was auto-provisioned as a processor and is logged in.
    u = auth.get_user_by_email(email)
    assert u is not None and u["role"] == "processor"
    with client.session_transaction() as s:
        assert s.get("user") == email
        assert s.get("role") == "processor"
        assert not s.get("sso_state")   # cleared
    con = auth.connect(); con.execute("DELETE FROM users WHERE username=?", (email,))
    con.commit(); con.close()


def test_sso_callback_out_of_domain_refused(client, monkeypatch):
    _configure_sso()
    auth.set_setting("sso_allowed_domains", "example.com")
    _patch_discovery(monkeypatch)
    email = "intruder@evil.com"
    monkeypatch.setattr(sso, "resolve_identity",
                        lambda p, code, ru: {"ok": True, "email": email, "name": "",
                                             "reason": ""})
    with client.session_transaction() as s:
        s["sso_state"] = "ok-state"
        s["sso_pending_provider"] = "google"
        s.pop("user", None)
    r = client.get("/sso/callback?state=ok-state&code=goodcode")
    assert r.status_code == 200
    assert "not permitted" in r.get_data(as_text=True)
    assert auth.get_user_by_email(email) is None   # never provisioned
    with client.session_transaction() as s:
        assert not s.get("user")


def test_login_page_shows_sso_button_only_when_enabled(client, monkeypatch):
    # Disabled: no button.
    body = client.get("/login").get_data(as_text=True)
    assert "/sso/login" not in body
    # Enabled: button present.
    _configure_sso()
    _patch_discovery(monkeypatch)
    body = client.get("/login").get_data(as_text=True)
    assert "/sso/login" in body
