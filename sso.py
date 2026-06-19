"""
sso.py — OPTIONAL single sign-on via a thin, self-built OpenID Connect connector.

A standards-only OIDC Authorization-Code connector that works with Google Workspace,
Microsoft Entra ID (Azure AD / Microsoft 365) and any standard OIDC provider — the admin
just pastes the provider's ISSUER URL and we discover its endpoints from
`<issuer>/.well-known/openid-configuration`.

MULTI-PROVIDER: Google AND Microsoft AND a custom OIDC provider can ALL be configured and
used at the same time (two/three "Sign in with …" buttons, each working independently).
Each provider key ("google" / "microsoft" / "custom") carries its OWN independent settings:
    sso_<p>_enabled            on/off (default off)
    sso_<p>_client_id
    sso_<p>_client_secret_sealed   base64(sealed blob), aad="sso:<p>:client_secret"
    sso_<p>_issuer             discovery URL (google has a fixed preset, override allowed;
                               microsoft is tenant-specific and REQUIRED; custom = blank)
SHARED/global settings (unchanged): the master `sso_enabled` switch, `sso_allowed_domains`,
`sso_auto_provision`.

HARD INVARIANTS (mirror the local-login fallback — the admin can never be locked out):
  * DEFAULT OFF. The master `sso_enabled` defaults off; a provider is usable only when the
    master switch is ON *and* that provider's own `sso_<p>_enabled` is ON *and* its issuer +
    client_id + a sealed client secret are configured. When nothing is usable the /sso/*
    routes do nothing (the app redirects them to /login) and behaviour is byte-identical.
  * Each client secret is NEVER stored in plaintext and NEVER logged. It is sealed at rest
    with keyvault ENVELOPE encryption (a fresh AES-256-GCM DEK per seal, wrapped by the
    KEK), stored base64 under `sso_<p>_client_secret_sealed` (aad="sso:<p>:client_secret").
    It is only ever decrypted in-process for the back-channel token exchange; the UI sees
    only set/!set, never the value.
  * Back-channel userinfo: we read identity from the secret-authenticated code exchange
    (server-to-server over TLS) + the userinfo endpoint, which AVOIDS hand-rolling JWT
    signature verification. The `nonce` is stashed in the session as CSRF/replay defence
    on the redirect (the matching `state` is the hard gate enforced on callback). The CHOSEN
    PROVIDER is also stashed in the session by the route, never trusted from the callback URL.
  * Auto-provisioned SSO users get role 'processor' ONLY and ONLY when their verified email
    domain is in the admin allowlist (`sso_allowed_domains`). No match -> login refused.

BACKWARD COMPATIBILITY (read-shim): an existing deployment may have the LEGACY single-provider
settings set (`sso_issuer` / `sso_client_id` / `sso_client_secret_sealed`, with `sso_provider`
naming which provider it was). If a provider's NEW per-provider settings are ABSENT but the
legacy settings exist AND legacy `sso_provider` maps to that provider key, the legacy values
are honoured for it (issuer / client_id / sealed secret). The legacy settings are NEVER
deleted. Net effect: a currently-working single-provider SSO keeps working with ZERO admin
action; the admin can then add the second provider. See `_legacy_provider()` / the per-field
shims below.

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

# Provider presets — (label, default_issuer) per key. Stable ORDER (google, microsoft,
# custom) is the order buttons render in. Google's issuer is a fixed preset (override
# allowed); Microsoft's is tenant-templated and must be supplied; custom is blank.
PROVIDER_PRESETS = {
    "google":    ("Google", "https://accounts.google.com"),
    "microsoft": ("Microsoft",
                  "https://login.microsoftonline.com/<tenant>/v2.0"),
    "custom":    ("Custom OIDC", ""),
}
PROVIDER_KEYS = ("google", "microsoft", "custom")

# Legacy single-provider settings (pre-multi-provider). Kept readable for the back-compat
# shim; never written by the new save path, never deleted.
LEGACY_SECRET_SETTING = "sso_client_secret_sealed"
LEGACY_SECRET_AAD = "sso:client_secret"

# Discovery document cache: issuer -> (expires_at, doc). Short in-process TTL; never
# persisted. A failed fetch returns None and does NOT poison the cache.
_DISCOVERY_TTL = 300                            # seconds
_disc_cache = {}


def _pkey(p):
    """Normalise a provider key to a known one ('custom' for anything unknown)."""
    p = (p or "").strip().lower()
    return p if p in PROVIDER_PRESETS else "custom"


# ---------------------------------------------------------------- legacy back-compat shim
def _legacy_provider():
    """Which provider key the LEGACY single-provider config named, or None. The legacy
    `sso_provider` setting historically stored the preset LABEL (e.g. 'Google Workspace'),
    so we map by both key and label substring; an issuer at accounts.google.com /
    login.microsoftonline.com is a further fallback."""
    raw = (auth.get_setting("sso_provider", "") or "").strip().lower()
    if not raw:
        # No explicit provider — infer from the legacy issuer if we can.
        iss = (auth.get_setting("sso_issuer", "") or "").strip().lower()
        if "accounts.google.com" in iss:
            return "google"
        if "login.microsoftonline.com" in iss:
            return "microsoft"
        if iss:
            return "custom"
        return None
    if "google" in raw:
        return "google"
    if "microsoft" in raw or "entra" in raw or "azure" in raw:
        return "microsoft"
    if raw in PROVIDER_PRESETS:
        return raw
    return "custom"


def _legacy_active_for(p):
    """True iff the LEGACY single-provider config maps to provider `p` (used to decide
    whether to fall back to legacy values for that provider's per-field reads)."""
    return _legacy_provider() == _pkey(p)


# ---------------------------------------------------------------- per-provider config
def provider_enabled_flag(p):
    """The provider's OWN on/off flag (independent of the master switch / completeness)."""
    p = _pkey(p)
    v = auth.get_setting(f"sso_{p}_enabled", None)
    if v is None:
        # New flag absent — inherit the legacy master switch ONLY for the legacy provider,
        # so a pre-existing single-provider deployment stays enabled with no admin action.
        if _legacy_active_for(p):
            return (auth.get_setting("sso_enabled", "off") or "off") == "on"
        return False
    return (v or "off") == "on"


def _issuer(p):
    p = _pkey(p)
    raw = auth.get_setting(f"sso_{p}_issuer", None)
    if raw is None and _legacy_active_for(p):                # back-compat shim
        raw = auth.get_setting("sso_issuer", "")
    return (raw or "").strip().rstrip("/")


def _client_id(p):
    p = _pkey(p)
    raw = auth.get_setting(f"sso_{p}_client_id", None)
    if raw is None and _legacy_active_for(p):                # back-compat shim
        raw = auth.get_setting("sso_client_id", "")
    return (raw or "").strip()


def _secret_setting(p):
    return f"sso_{_pkey(p)}_client_secret_sealed"


def _secret_aad(p):
    return f"sso:{_pkey(p)}:client_secret"


def has_secret(p):
    """True iff a sealed client secret is stored for provider `p`. Never decrypts here
    (cheap probe). Falls back to the legacy sealed secret for the legacy provider."""
    p = _pkey(p)
    if (auth.get_setting(_secret_setting(p), "") or "").strip():
        return True
    if _legacy_active_for(p):                                # back-compat shim
        return bool((auth.get_setting(LEGACY_SECRET_SETTING, "") or "").strip())
    return False


def set_secret(p, plaintext):
    """Seal+store provider `p`'s client secret (or CLEAR it when blank). Sealed with
    keyvault envelope encryption, bound to the per-provider SSO context via aad. Never
    logs the value."""
    p = _pkey(p)
    plaintext = (plaintext or "").strip()
    if not plaintext:
        auth.set_setting(_secret_setting(p), "")
        return "cleared"
    blob = keyvault.seal(plaintext, _secret_aad(p))
    auth.set_setting(_secret_setting(p), base64.b64encode(blob).decode("ascii"))
    return "saved"


def _client_secret(p):
    """Decrypt provider `p`'s stored client secret for the back-channel token exchange,
    or None. Never raises (a bad/undecryptable blob is logged and treated as unconfigured)
    and NEVER logs the plaintext. Falls back to the legacy sealed secret for the legacy
    provider when no per-provider secret is stored."""
    p = _pkey(p)
    raw = (auth.get_setting(_secret_setting(p), "") or "").strip()
    aad = _secret_aad(p)
    if not raw and _legacy_active_for(p):                    # back-compat shim
        raw = (auth.get_setting(LEGACY_SECRET_SETTING, "") or "").strip()
        aad = LEGACY_SECRET_AAD
    if not raw:
        return None
    try:
        return keyvault.open(base64.b64decode(raw.encode("ascii")), aad)
    except Exception as e:
        log.warning("could not open sealed SSO client secret for %s: %s", p, e)
        return None


# ---------------------------------------------------------------- shared/global config
def allowed_domains():
    """Lowercased list of allowed email domains (empty = no domain restriction). GLOBAL."""
    raw = (auth.get_setting("sso_allowed_domains", "") or "")
    return [d.strip().lower() for d in raw.replace(";", ",").split(",") if d.strip()]


def auto_provision():
    """Whether to auto-create an SSO user on first login (default ON per the product
    decision; still gated by the domain allowlist). GLOBAL."""
    return (auth.get_setting("sso_auto_provision", "on") or "on") != "off"


def master_enabled():
    """The master SSO switch (default off). GLOBAL."""
    return (auth.get_setting("sso_enabled", "off") or "off") == "on"


def provider_enabled(p):
    """Is provider `p` usable? master `sso_enabled` ON *and* its own `sso_<p>_enabled` ON
    *and* an issuer + client_id + a sealed secret are all configured. Never raises."""
    try:
        p = _pkey(p)
        if not master_enabled():
            return False
        if not provider_enabled_flag(p):
            return False
        return bool(_issuer(p) and _client_id(p) and has_secret(p))
    except Exception as e:
        log.warning("sso.provider_enabled(%s) check failed, treating as OFF: %s", p, e)
        return False


def enabled_providers():
    """The providers that pass provider_enabled, as (key, label) in the stable preset
    order (google, microsoft, custom). Never raises -> [] on any error."""
    out = []
    try:
        for k in PROVIDER_KEYS:
            if provider_enabled(k):
                out.append((k, PROVIDER_PRESETS[k][0]))
    except Exception as e:
        log.warning("sso.enabled_providers failed, treating as none: %s", e)
        return []
    return out


def enabled():
    """Is SSO usable AT ALL (any provider)? Kept so existing call-sites still work."""
    return bool(enabled_providers())


def config():
    """The NON-SECRET settings for the Admin panel. Per-provider state plus the shared
    fields; no plaintext secret is ever returned here. Each provider reports issuer /
    client_id / has_secret / enabled (its own flag) / active (fully usable)."""
    providers = {}
    for k in PROVIDER_KEYS:
        iss = _issuer(k)
        if not iss and k == "google":          # prefill the fixed Google preset in the form
            iss = PROVIDER_PRESETS["google"][1]
        providers[k] = {
            "label":      PROVIDER_PRESETS[k][0],
            "default_issuer": PROVIDER_PRESETS[k][1],
            "enabled":    provider_enabled_flag(k),
            "issuer":     iss,
            "client_id":  _client_id(k),
            "has_secret": has_secret(k),
            "active":     provider_enabled(k),
        }
    return {
        "enabled":         enabled(),
        "master_enabled":  master_enabled(),
        "providers":       providers,
        "allowed_domains": allowed_domains(),
        "auto_provision":  auto_provision(),
    }


# ---------------------------------------------------------------- OIDC discovery
def _discovery(p=None, issuer=None):
    """Fetch + cache `<issuer>/.well-known/openid-configuration`. The issuer is resolved
    from provider `p` (or an explicit `issuer`). Returns the discovery dict (with
    authorization_endpoint/token_endpoint/userinfo_endpoint) or None. Cached in-process
    with a short TTL keyed by issuer. Never raises."""
    iss = (issuer or _issuer(p)).rstrip("/")
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
def login_url(p, redirect_uri, state, nonce):
    """Build provider `p`'s AUTHORIZE URL (response_type=code, scope=openid email profile,
    prompt=select_account). Returns None if the provider is unconfigured / discovery fails."""
    from urllib.parse import urlencode
    p = _pkey(p)
    doc = _discovery(p)
    if not doc:
        return None
    params = {
        "response_type": "code",
        "scope": "openid email profile",
        "client_id": _client_id(p),
        "redirect_uri": redirect_uri,
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    }
    sep = "&" if "?" in doc["authorization_endpoint"] else "?"
    return doc["authorization_endpoint"] + sep + urlencode(params)


def exchange_code(p, code, redirect_uri):
    """POST the authorization code to provider `p`'s token endpoint (secret-authenticated,
    over TLS) and return the tokens dict. Returns None on any failure (never raises)."""
    p = _pkey(p)
    doc = _discovery(p)
    secret = _client_secret(p)
    if not doc or not doc.get("token_endpoint") or not secret:
        return None
    try:
        import requests
        r = requests.post(doc["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": _client_id(p),
            "client_secret": secret,
        }, headers={"Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        # NEVER include the secret/code in the log line.
        log.warning("SSO token exchange failed for %s: %s", p, e)
        return None


def userinfo(p, access_token):
    """GET provider `p`'s userinfo endpoint with the Bearer access token -> claims dict
    (email, email_verified, name, ...). Returns None on any failure (never raises)."""
    p = _pkey(p)
    doc = _discovery(p)
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
        log.warning("SSO userinfo fetch failed for %s: %s", p, e)
        return None


def resolve_identity(p, code, redirect_uri):
    """Orchestrate exchange_code -> userinfo for provider `p` and return a normalized
    identity:
        {"ok": bool, "email": str, "name": str, "reason": str}
    Requires a non-empty email; if the provider reports email_verified it MUST be true.
    The email is lowercased. Never raises."""
    try:
        p = _pkey(p)
        tokens = exchange_code(p, code, redirect_uri)
        if not tokens or not tokens.get("access_token"):
            return {"ok": False, "reason": "could not complete the sign-in handshake "
                                           "with the identity provider"}
        info = userinfo(p, tokens["access_token"])
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


def auto_provision_allowed(email):
    """May a NEW account be auto-created for this email? Requires auto-provision ON *and* an
    EXPLICIT domain allowlist that the email matches. A BLANK allowlist NEVER auto-creates —
    otherwise anyone with a Google/Microsoft account could self-provision into the system.
    (Existing users can still sign in regardless of this.)"""
    if not auto_provision():
        return False
    if not allowed_domains():                 # blank allowlist -> no open self-provisioning
        return False
    return domain_allowed(email)
