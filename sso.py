"""
sso.py — OPTIONAL single sign-on via a thin, self-built OpenID Connect connector.

A standards-only OIDC Authorization-Code connector that works with Google Workspace,
Microsoft Entra ID (Azure AD / Microsoft 365) and any standard OIDC provider — the admin
just pastes the provider's ISSUER URL and we discover its endpoints from
`<issuer>/.well-known/openid-configuration`.

HARD INVARIANTS (mirror the local-login fallback — the admin can never be locked out):
  * DEFAULT OFF. `sso_enabled` defaults off; `enabled()` ALSO requires an issuer, a
    client_id and a sealed client secret to be configured. When off the /sso/* routes
    do nothing (the app redirects them to /login) and behaviour is byte-identical.
  * The client secret is NEVER stored in plaintext and NEVER logged. It is sealed at rest
    with keyvault ENVELOPE encryption (a fresh AES-256-GCM DEK per seal, wrapped by the
    KEK), stored base64 under `sso_client_secret_sealed` (aad="sso:client_secret"). It is
    only ever decrypted in-process for the back-channel token exchange; the UI sees only
    set/!set, never the value.
  * Back-channel userinfo: we read identity from the secret-authenticated code exchange
    (server-to-server over TLS) + the userinfo endpoint, which AVOIDS hand-rolling JWT
    signature verification. The `nonce` is stashed in the session as CSRF/replay defence
    on the redirect (the matching `state` is the hard gate enforced on callback).
  * Auto-provisioned SSO users get role 'processor' ONLY and ONLY when their verified email
    domain is in the admin allowlist (`sso_allowed_domains`). No match -> login refused.

Outbound HTTP reuses `requests` (same as ai_review.py / extract.py — no new dependency).
Nothing here ever raises into a web request: every public entry point maps failures to a
safe value (False / None / {"ok": False, ...}) and logs via applog.
"""
import base64
import os
import time

import auth
import keyvault
import applog

log = applog.get("sso")

WORKDIR = os.path.dirname(os.path.abspath(__file__))

SECRET_SETTING = "sso_client_secret_sealed"   # base64(sealed blob) in app_settings
_SECRET_AAD = "sso:client_secret"

# Discovery document cache: issuer -> (expires_at, doc). Short in-process TTL; never
# persisted. A failed fetch returns None and does NOT poison the cache.
_DISCOVERY_TTL = 300                            # seconds
_disc_cache = {}

# Provider presets — the issuer hint the Admin panel prefills. "custom" = blank.
PROVIDER_PRESETS = {
    "google":    ("Google Workspace", "https://accounts.google.com"),
    "microsoft": ("Microsoft Entra ID",
                  "https://login.microsoftonline.com/<tenant>/v2.0"),
    "custom":    ("Custom OIDC provider", ""),
}


# ---------------------------------------------------------------- configuration
def _issuer():
    return (auth.get_setting("sso_issuer", "") or "").strip().rstrip("/")


def _client_id():
    return (auth.get_setting("sso_client_id", "") or "").strip()


def has_secret():
    """True iff a sealed client secret is stored. Never decrypts here (cheap probe)."""
    return bool((auth.get_setting(SECRET_SETTING, "") or "").strip())


def set_secret(plaintext):
    """Seal+store the client secret (or CLEAR it when blank). Sealed with keyvault
    envelope encryption, bound to the SSO context via aad. Never logs the value."""
    plaintext = (plaintext or "").strip()
    if not plaintext:
        auth.set_setting(SECRET_SETTING, "")
        return "cleared"
    blob = keyvault.seal(plaintext, _SECRET_AAD)
    auth.set_setting(SECRET_SETTING, base64.b64encode(blob).decode("ascii"))
    return "saved"


def _client_secret():
    """Decrypt the stored client secret for the back-channel token exchange, or None.
    Never raises (a bad/undecryptable blob is logged and treated as unconfigured) and
    NEVER logs the plaintext."""
    raw = (auth.get_setting(SECRET_SETTING, "") or "").strip()
    if not raw:
        return None
    try:
        return keyvault.open(base64.b64decode(raw.encode("ascii")), _SECRET_AAD)
    except Exception as e:
        log.warning("could not open sealed SSO client secret: %s", e)
        return None


def allowed_domains():
    """Lowercased list of allowed email domains (empty = no domain restriction)."""
    raw = (auth.get_setting("sso_allowed_domains", "") or "")
    return [d.strip().lower() for d in raw.replace(";", ",").split(",") if d.strip()]


def auto_provision():
    """Whether to auto-create an SSO user on first login (default ON per the product
    decision; still gated by the domain allowlist)."""
    return (auth.get_setting("sso_auto_provision", "on") or "on") != "off"


def enabled():
    """Is SSO usable? `sso_enabled` ON *and* an issuer + client_id + a sealed secret are
    all configured. Never raises -> False on any error."""
    try:
        if (auth.get_setting("sso_enabled", "off") or "off") != "on":
            return False
        return bool(_issuer() and _client_id() and has_secret())
    except Exception as e:
        log.warning("sso.enabled check failed, treating as OFF: %s", e)
        return False


def config():
    """The NON-SECRET settings for the Admin panel. The secret is reported only as a
    boolean (has_secret) — the plaintext is never returned here."""
    return {
        "enabled":       enabled(),
        "provider":      (auth.get_setting("sso_provider", "") or "").strip(),
        "issuer":        _issuer(),
        "client_id":     _client_id(),
        "allowed_domains": allowed_domains(),
        "auto_provision": auto_provision(),
        "has_secret":    has_secret(),
    }


# ---------------------------------------------------------------- OIDC discovery
def _discovery(issuer=None):
    """Fetch + cache `<issuer>/.well-known/openid-configuration`. Returns the discovery
    dict (with authorization_endpoint/token_endpoint/userinfo_endpoint) or None. Cached
    in-process with a short TTL. Never raises."""
    iss = (issuer or _issuer()).rstrip("/")
    if not iss:
        return None
    now = time.time()
    cached = _disc_cache.get(iss)
    if cached and cached[0] > now:
        return cached[1]
    try:
        import requests
        url = iss + "/.well-known/openid-configuration"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        doc = r.json()
        if not isinstance(doc, dict) or not doc.get("authorization_endpoint"):
            log.warning("OIDC discovery for %s missing authorization_endpoint", iss)
            return None
        _disc_cache[iss] = (now + _DISCOVERY_TTL, doc)
        return doc
    except Exception as e:
        log.warning("OIDC discovery fetch failed for %s: %s", iss, e)
        return None


# ---------------------------------------------------------------- flow
def login_url(redirect_uri, state, nonce):
    """Build the provider AUTHORIZE URL (response_type=code, scope=openid email profile,
    prompt=select_account). Returns None if SSO is unconfigured / discovery fails."""
    from urllib.parse import urlencode
    doc = _discovery()
    if not doc:
        return None
    params = {
        "response_type": "code",
        "scope": "openid email profile",
        "client_id": _client_id(),
        "redirect_uri": redirect_uri,
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    }
    sep = "&" if "?" in doc["authorization_endpoint"] else "?"
    return doc["authorization_endpoint"] + sep + urlencode(params)


def exchange_code(code, redirect_uri):
    """POST the authorization code to the token endpoint (secret-authenticated, over TLS)
    and return the tokens dict. Returns None on any failure (never raises)."""
    doc = _discovery()
    secret = _client_secret()
    if not doc or not doc.get("token_endpoint") or not secret:
        return None
    try:
        import requests
        r = requests.post(doc["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": _client_id(),
            "client_secret": secret,
        }, headers={"Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        # NEVER include the secret/code in the log line.
        log.warning("SSO token exchange failed: %s", e)
        return None


def userinfo(access_token):
    """GET the userinfo endpoint with the Bearer access token -> claims dict (email,
    email_verified, name, ...). Returns None on any failure (never raises)."""
    doc = _discovery()
    if not doc or not doc.get("userinfo_endpoint") or not access_token:
        return None
    try:
        import requests
        r = requests.get(doc["userinfo_endpoint"],
                         headers={"Authorization": "Bearer " + access_token},
                         timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("SSO userinfo fetch failed: %s", e)
        return None


def resolve_identity(code, redirect_uri):
    """Orchestrate exchange_code -> userinfo and return a normalized identity:
        {"ok": bool, "email": str, "name": str, "reason": str}
    Requires a non-empty email; if the provider reports email_verified it MUST be true.
    The email is lowercased. Never raises."""
    try:
        tokens = exchange_code(code, redirect_uri)
        if not tokens or not tokens.get("access_token"):
            return {"ok": False, "reason": "could not complete the sign-in handshake "
                                           "with the identity provider"}
        info = userinfo(tokens["access_token"])
        if not info:
            return {"ok": False, "reason": "could not read your profile from the "
                                           "identity provider"}
        email = (info.get("email") or "").strip().lower()
        if not email:
            return {"ok": False, "reason": "the identity provider did not return an "
                                           "email address"}
        # email_verified, when present, must be true (Google/Entra both send it).
        ev = info.get("email_verified")
        if ev is not None and str(ev).lower() not in ("true", "1"):
            return {"ok": False, "reason": "your email address is not verified at the "
                                           "identity provider"}
        return {"ok": True, "email": email,
                "name": (info.get("name") or "").strip(), "reason": ""}
    except Exception as e:
        log.warning("resolve_identity failed: %s", e)
        return {"ok": False, "reason": "an unexpected error occurred during sign-in"}


def domain_allowed(email):
    """True if the allowlist is empty (no restriction) OR the email's domain is in it
    (case-insensitive). A malformed/empty email is refused when an allowlist is set."""
    doms = allowed_domains()
    if not doms:
        return True
    email = (email or "").strip().lower()
    at = email.rfind("@")
    if at < 0:
        return False
    return email[at + 1:] in doms
