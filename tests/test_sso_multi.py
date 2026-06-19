"""
Tests for the MULTI-PROVIDER SSO connector — Google AND Microsoft (AND custom) can all be
configured and used at the same time. No real network is ever hit (sso._discovery is
monkeypatched, per the existing test_sso.py patterns). Exercises:
  * provider_enabled is true ONLY when that provider is fully configured AND the master
    switch is on;
  * enabled_providers lists BOTH when both are configured (stable order);
  * login_url builds the right authorize URL per provider;
  * the /sso/callback uses the SESSION provider, not a spoofed query param;
  * the LEGACY single-provider read-shim still resolves as the mapped provider;
  * default (master off) -> enabled_providers() empty AND the login page renders no button.
"""
import auth
import sso


# ---------------------------------------------------------------- helpers
def _disc_for(p=None, issuer=None):
    """A per-provider discovery doc keyed by issuer, so login_url builds the right URL."""
    iss = (issuer or sso._issuer(p) or "").rstrip("/")
    if "accounts.google.com" in iss:
        return {
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
        }
    if "login.microsoftonline.com" in iss:
        return {
            "authorization_endpoint":
                "https://login.microsoftonline.com/tenant-x/oauth2/v2.0/authorize",
            "token_endpoint": "https://login.microsoftonline.com/tenant-x/oauth2/v2.0/token",
            "userinfo_endpoint": "https://graph.microsoft.com/oidc/userinfo",
        }
    if iss:
        return {
            "authorization_endpoint": iss + "/authorize",
            "token_endpoint": iss + "/token",
            "userinfo_endpoint": iss + "/userinfo",
        }
    return None


def _patch_disc(monkeypatch):
    monkeypatch.setattr(sso, "_discovery", _disc_for)


def _configure_google():
    auth.set_setting("sso_enabled", "on")
    auth.set_setting("sso_google_enabled", "on")
    auth.set_setting("sso_google_issuer", "https://accounts.google.com")
    auth.set_setting("sso_google_client_id", "google-client-id")
    sso.set_secret("google", "google-secret")


def _configure_microsoft():
    auth.set_setting("sso_enabled", "on")
    auth.set_setting("sso_microsoft_enabled", "on")
    auth.set_setting("sso_microsoft_issuer",
                     "https://login.microsoftonline.com/tenant-x/v2.0")
    auth.set_setting("sso_microsoft_client_id", "ms-client-id")
    sso.set_secret("microsoft", "ms-secret")


# ---------------------------------------------------------------- provider_enabled
def test_provider_enabled_requires_full_config_and_master():
    # Nothing configured -> off.
    assert sso.provider_enabled("google") is False
    auth.set_setting("sso_enabled", "on")
    auth.set_setting("sso_google_enabled", "on")
    auth.set_setting("sso_google_issuer", "https://accounts.google.com")
    auth.set_setting("sso_google_client_id", "gid")
    # Missing secret -> still off.
    assert sso.provider_enabled("google") is False
    sso.set_secret("google", "s3cr3t")
    assert sso.provider_enabled("google") is True
    # Master off makes it inert even when the provider is complete + its own flag on.
    auth.set_setting("sso_enabled", "off")
    assert sso.provider_enabled("google") is False
    auth.set_setting("sso_enabled", "on")
    # The provider's OWN flag off makes only it inert.
    auth.set_setting("sso_google_enabled", "off")
    assert sso.provider_enabled("google") is False


def test_microsoft_independent_of_google():
    _configure_microsoft()
    assert sso.provider_enabled("microsoft") is True
    # Google is NOT configured -> still off (independent).
    assert sso.provider_enabled("google") is False


# ---------------------------------------------------------------- enabled_providers
def test_enabled_providers_lists_both_in_stable_order():
    _configure_google()
    _configure_microsoft()
    provs = sso.enabled_providers()
    keys = [k for k, _lbl in provs]
    assert keys == ["google", "microsoft"]          # stable preset order
    assert dict(provs)["google"] == "Google"
    assert dict(provs)["microsoft"] == "Microsoft"
    assert sso.enabled() is True


def test_enabled_providers_empty_by_default():
    # conftest clears sso_* before each test -> master off, nothing usable.
    assert sso.enabled_providers() == []
    assert sso.enabled() is False


# ---------------------------------------------------------------- login_url per provider
def test_login_url_builds_right_authorize_per_provider(monkeypatch):
    _configure_google()
    _configure_microsoft()
    _patch_disc(monkeypatch)
    gurl = sso.login_url("google", "https://app/cb", "st", "no")
    assert gurl.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "client_id=google-client-id" in gurl
    assert "response_type=code" in gurl and "nonce=no" in gurl
    murl = sso.login_url("microsoft", "https://app/cb", "st", "no")
    assert murl.startswith(
        "https://login.microsoftonline.com/tenant-x/oauth2/v2.0/authorize")
    assert "client_id=ms-client-id" in murl


# ---------------------------------------------------------------- callback uses SESSION
def test_callback_uses_session_provider_not_query(client, monkeypatch):
    # BOTH providers usable; resolve_identity records which provider it was called with.
    _configure_google()
    _configure_microsoft()
    auth.set_setting("sso_allowed_domains", "example.com")
    _patch_disc(monkeypatch)
    seen = {}
    email = "person@example.com"
    con = auth.connect(); con.execute("DELETE FROM users WHERE username=?", (email,))
    con.commit(); con.close()

    def _fake_resolve(p, code, ru):
        seen["provider"] = p
        return {"ok": True, "email": email, "name": "P", "reason": ""}

    monkeypatch.setattr(sso, "resolve_identity", _fake_resolve)
    # Session says microsoft; the query string tries to spoof google — must be ignored.
    with client.session_transaction() as s:
        s["sso_state"] = "match"
        s["sso_pending_provider"] = "microsoft"
    r = client.get("/sso/callback?state=match&code=ok&provider=google")
    assert r.status_code == 302
    assert seen["provider"] == "microsoft"          # SESSION wins, not the query param
    con = auth.connect(); con.execute("DELETE FROM users WHERE username=?", (email,))
    con.commit(); con.close()


def test_login_route_rejects_unknown_provider(client, monkeypatch):
    _configure_google()
    _patch_disc(monkeypatch)
    # microsoft isn't enabled -> not a valid choice.
    r = client.get("/sso/login?provider=microsoft")
    assert r.status_code == 200
    assert "choose a sign-in provider" in r.get_data(as_text=True)
    with client.session_transaction() as s:
        assert not s.get("sso_pending_provider")


# ---------------------------------------------------------------- legacy back-compat shim
def test_legacy_single_provider_shim_resolves_as_mapped_provider():
    # An OLD deployment: only the legacy single-provider settings are set (no per-provider
    # keys), with sso_provider naming Google. The shim must keep it working as 'google'.
    auth.set_setting("sso_enabled", "on")
    auth.set_setting("sso_provider", "Google Workspace")
    auth.set_setting("sso_issuer", "https://accounts.google.com")
    auth.set_setting("sso_client_id", "legacy-gid")
    sso.set_secret("google", "")                     # ensure no per-provider secret first
    # Seal the secret under the LEGACY setting/aad, exactly as the old code did.
    import base64, keyvault
    blob = keyvault.seal("legacy-secret", sso.LEGACY_SECRET_AAD)
    auth.set_setting(sso.LEGACY_SECRET_SETTING, base64.b64encode(blob).decode("ascii"))
    # No per-provider google settings exist -> the shim honours the legacy values.
    assert sso._issuer("google") == "https://accounts.google.com"
    assert sso._client_id("google") == "legacy-gid"
    assert sso.has_secret("google") is True
    assert sso._client_secret("google") == "legacy-secret"
    assert sso.provider_enabled("google") is True
    # And the shim does NOT bleed into a different provider.
    assert sso.provider_enabled("microsoft") is False
    keys = [k for k, _ in sso.enabled_providers()]
    assert keys == ["google"]


def test_legacy_microsoft_shim_inferred_from_issuer():
    auth.set_setting("sso_enabled", "on")
    auth.set_setting("sso_issuer",
                     "https://login.microsoftonline.com/common/v2.0")
    auth.set_setting("sso_client_id", "legacy-msid")
    import base64, keyvault
    blob = keyvault.seal("ms-legacy", sso.LEGACY_SECRET_AAD)
    auth.set_setting(sso.LEGACY_SECRET_SETTING, base64.b64encode(blob).decode("ascii"))
    assert sso._legacy_provider() == "microsoft"
    assert sso.provider_enabled("microsoft") is True
    assert sso.provider_enabled("google") is False


# ---------------------------------------------------------------- login page byte-identity
def test_login_page_no_button_when_master_off(client):
    # Default (master off) -> no SSO button at all (byte-identical to the no-SSO page).
    body = client.get("/login").get_data(as_text=True)
    # The .ssobtn/.ssosep CSS classes are always present in the page <style>; assert that
    # no rendered SSO ELEMENT exists (the button anchor / the separator <p>).
    assert "/sso/login" not in body
    assert '<p class="ssosep">' not in body
    assert "Sign in with" not in body


def test_login_page_two_buttons_when_both_enabled(client, monkeypatch):
    _configure_google()
    _configure_microsoft()
    _patch_disc(monkeypatch)
    body = client.get("/login").get_data(as_text=True)
    assert "/sso/login?provider=google" in body
    assert "/sso/login?provider=microsoft" in body
    assert "Sign in with Google" in body
    assert "Sign in with Microsoft" in body
    # The separator shows once a provider button renders.
    assert "ssosep" in body
