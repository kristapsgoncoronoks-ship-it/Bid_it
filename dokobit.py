"""
dokobit.py — OPTIONAL, default-OFF integration seam for ISSUING and SIGNING customer
invoices via the Dokobit Gateway API (https://gateway.dokobit.com).

This is the OUTBOUND e-signature/e-delivery seam for the customer service-fee invoice
(`invoice_issue.py` builds the document; this module uploads it, opens a Dokobit signing
and tracks it to a signed PDF). It mirrors the existing provider-seam pattern of
finance.py / bank_recon.py (a NULL default that moves nothing) and sso.py (a sealed
credential + an env toggle, outbound HTTP via `requests`):

  * DEFAULT OFF. `dokobit_enabled` defaults off; `enabled()` ALSO requires a sealed API
    token AND `requests` to be importable. When OFF every method returns
    {"ok": False, "error": "Dokobit not configured"} and makes NO network call — the app
    is byte-identical to today.
  * The API access token is NEVER stored in plaintext and NEVER logged. It is sealed at
    rest with keyvault ENVELOPE encryption (a fresh AES-256-GCM DEK per seal, wrapped by
    the KEK), stored base64 under `dokobit_token_sealed` (aad="dokobit:token"). It is only
    ever decrypted in-process for the call; the UI sees only set/!set, never the value.
  * NEVER RAISES into a web request: every public entry point maps a failure to a safe
    {"ok": False, "error": ...} dict (or None for byte fetches) and logs via applog —
    NEVER the token.

DOKOBIT GATEWAY API SHAPES (researched; HTTP via `requests`):
  * Base URL by env: sandbox `https://gateway-sandbox.dokobit.com`,
    production `https://gateway.dokobit.com` (the `dokobit_env` setting; default sandbox).
  * Auth: the API access token is passed as the query param `?access_token=<TOKEN>` on
    EVERY API call (never a header, never logged).
  * Upload:  POST /api/file/upload.json?access_token=...   body file[content]=base64(bytes),
             file[name]=filename  ->  {"status":"ok","token":<file token>}.
  * Create:  POST /api/signing/create.json?access_token=...  body type, files[], signers[],
             postback_url, return_url  ->  {"status":"ok","token":<signing_token>,
             "signers":[{"id":..,"access_token":<signer token>}, ...]}.
             The per-signer signing URL = <base>/signing/<signing_token>?access_token=<signer>.
  * Status:  GET  /api/signing/<signing_token>/status.json?access_token=...
             -> {"status":"completed"|..., "file":<signed file url>}.
  * Postback: Dokobit POSTs `postback_url` on signer_signed / signing_completed /
             signing_archived / signing_archive_failed; on signing_completed the payload
             carries a `file` URL to fetch the signed document.

The signature `type` for a QUALIFIED (QES / PAdES) PDF signing is "pdf" per the Gateway
docs (this is the documented default we use; a deployment can override via the
`dokobit_sig_type` setting). FLAGGED for review — confirm against your Gateway contract.

The Identity Gateway (eID: Smart-ID / Mobile-ID authentication) is a SEPARATE Dokobit
service; `authenticate()` here is a documented STUB only (returns not-configured) — it is
deliberately not built in this seam.
"""
import base64
import os

import applog
import auth
import keyvault

log = applog.get("dokobit")

WORKDIR = os.path.dirname(os.path.abspath(__file__))

TOKEN_SETTING = "dokobit_token_sealed"      # base64(sealed blob) in app_settings
_TOKEN_AAD = "dokobit:token"

# Gateway base URLs by environment.
_BASES = {
    "sandbox":    "https://gateway-sandbox.dokobit.com",
    "production": "https://gateway.dokobit.com",
}
_DEFAULT_ENV = "sandbox"

# Default signature type for a QUALIFIED PDF (PAdES) signing. Overridable via setting.
_DEFAULT_SIG_TYPE = "pdf"

# Postback actions Dokobit may send.
ACTION_SIGNER_SIGNED = "signer_signed"
ACTION_COMPLETED = "signing_completed"
ACTION_ARCHIVED = "signing_archived"
ACTION_ARCHIVE_FAILED = "signing_archive_failed"

_HTTP_TIMEOUT = 30          # seconds, every outbound call
_OFF = {"ok": False, "error": "Dokobit not configured"}


# ---------------------------------------------------------------- configuration
def _env():
    """The configured environment key ('sandbox' | 'production'); default sandbox.
    An unknown value falls back to sandbox (never the production base by accident)."""
    v = (auth.get_setting("dokobit_env", _DEFAULT_ENV) or _DEFAULT_ENV).strip().lower()
    return v if v in _BASES else _DEFAULT_ENV


def _base():
    """The Gateway base URL for the configured environment (no trailing slash)."""
    return _BASES[_env()]


def _sig_type():
    return (auth.get_setting("dokobit_sig_type", _DEFAULT_SIG_TYPE)
            or _DEFAULT_SIG_TYPE).strip() or _DEFAULT_SIG_TYPE


def postback_url():
    """The admin-configured URL Dokobit POSTs signing events to (or "")."""
    return (auth.get_setting("dokobit_postback_url", "") or "").strip()


def return_url():
    """The admin-configured URL the signer is returned to after signing (or "")."""
    return (auth.get_setting("dokobit_return_url", "") or "").strip()


def has_token():
    """True iff a sealed API token is stored. Never decrypts here (cheap probe)."""
    return bool((auth.get_setting(TOKEN_SETTING, "") or "").strip())


def set_token(plaintext):
    """Seal+store the API access token (or CLEAR it when blank). Sealed with keyvault
    envelope encryption, bound to the Dokobit context via aad. Never logs the value."""
    plaintext = (plaintext or "").strip()
    if not plaintext:
        auth.set_setting(TOKEN_SETTING, "")
        return "cleared"
    blob = keyvault.seal(plaintext, _TOKEN_AAD)
    auth.set_setting(TOKEN_SETTING, base64.b64encode(blob).decode("ascii"))
    return "saved"


def _token():
    """Decrypt the stored API token for a single call, or None. Never raises (a
    bad/undecryptable blob is logged and treated as unconfigured) and NEVER logs it."""
    raw = (auth.get_setting(TOKEN_SETTING, "") or "").strip()
    if not raw:
        return None
    try:
        return keyvault.open(base64.b64decode(raw.encode("ascii")), _TOKEN_AAD)
    except Exception as e:
        log.warning("could not open sealed Dokobit token: %s", e)
        return None


def enabled():
    """Is the Dokobit seam usable? `dokobit_enabled` ON *and* a sealed token is stored
    *and* `requests` imports. Never raises -> False on any error. DEFAULT OFF."""
    try:
        if (auth.get_setting("dokobit_enabled", "off") or "off") != "on":
            return False
        if not has_token():
            return False
        import requests  # noqa: F401
        return True
    except Exception as e:
        log.warning("dokobit.enabled check failed, treating as OFF: %s", e)
        return False


def config():
    """The NON-SECRET settings for the Admin panel. The token is reported only as a
    boolean (has_token) — the plaintext is never returned here."""
    return {
        "enabled":      enabled(),
        "env":          _env(),
        "base":         _base(),
        "has_token":    has_token(),
        "sig_type":     _sig_type(),
        "postback_url": postback_url(),
        "return_url":   return_url(),
    }


# ---------------------------------------------------------------- HTTP helper
def _api_url(path):
    """Build a full API URL WITHOUT the token (the token is added as a param on the
    call so it never lands in a logged/built URL string)."""
    return _base() + path


def _post(path, *, json_body=None, data=None):
    """POST to a Gateway API path with the access_token query param. Returns the parsed
    JSON dict, or raises (the caller maps it to an error dict). NEVER logs the token."""
    import requests
    r = requests.post(_api_url(path), params={"access_token": _token()},
                      json=json_body, data=data, timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _get(path):
    import requests
    r = requests.get(_api_url(path), params={"access_token": _token()},
                     timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- API surface
def upload_file(content_bytes, filename):
    """Upload a document to Dokobit (base64 in file[content]). Returns
    {"ok": True, "token": <file token>} or {"ok": False, "error": ...}. Makes NO network
    call when the seam is OFF. Never raises."""
    if not enabled():
        return dict(_OFF)
    try:
        if not content_bytes:
            return {"ok": False, "error": "empty document"}
        b64 = base64.b64encode(bytes(content_bytes)).decode("ascii")
        out = _post("/api/file/upload.json",
                    data={"file[content]": b64, "file[name]": filename or "invoice.pdf"})
        tok = (out or {}).get("token")
        if not tok:
            return {"ok": False, "error": "Dokobit upload returned no file token",
                    "raw": out}
        return {"ok": True, "token": tok}
    except Exception as e:
        log.warning("dokobit.upload_file failed: %s", e)
        return {"ok": False, "error": f"upload failed: {e}"}


def create_signing(files, signers, postback_url=None, return_url=None, sig_type=None):
    """Open a Dokobit signing over `files` for `signers`.

      files   = [{"token": <file token>, "filename": "..."} , ...]
      signers = [{"name","surname","code","country","phone"?,"email"?}, ...]
                (email enables e-delivery of the signing invite/result)

    Returns {"ok": True, "signing_token": ..., "signers": [{"access_token", "sign_url"},
    ...]} or {"ok": False, "error": ...}. Makes NO network call when the seam is OFF.
    Never raises."""
    if not enabled():
        return dict(_OFF)
    try:
        if not files or not signers:
            return {"ok": False, "error": "files and signers are required"}
        body = {
            "type": (sig_type or _sig_type()),
            "files": [{"token": f.get("token"), "filename": f.get("filename")}
                      for f in files],
            "signers": [{k: s[k] for k in
                         ("name", "surname", "code", "country", "phone", "email")
                         if s.get(k)} for s in signers],
        }
        pb = postback_url if postback_url is not None else globals()["postback_url"]()
        ru = return_url if return_url is not None else globals()["return_url"]()
        if pb:
            body["postback_url"] = pb
        if ru:
            body["return_url"] = ru
        out = _post("/api/signing/create.json", json_body=body)
        signing_token = (out or {}).get("token") or (out or {}).get("signing_token")
        if not signing_token:
            return {"ok": False, "error": "Dokobit create returned no signing token",
                    "raw": out}
        base = _base()
        sgn = []
        for s in (out.get("signers") or []):
            at = s.get("access_token")
            sgn.append({
                "id": s.get("id"),
                "access_token": at,
                "sign_url": (f"{base}/signing/{signing_token}?access_token={at}"
                             if at else None),
            })
        return {"ok": True, "signing_token": signing_token, "signers": sgn}
    except Exception as e:
        log.warning("dokobit.create_signing failed: %s", e)
        return {"ok": False, "error": f"create signing failed: {e}"}


def signing_status(signing_token):
    """Poll a signing's status. Returns {"ok": True, "status": ...,
    "signed_file_url": <url|None>} or {"ok": False, "error": ...}. No network call when
    OFF. Never raises."""
    if not enabled():
        return dict(_OFF)
    try:
        if not signing_token:
            return {"ok": False, "error": "signing_token is required"}
        out = _get(f"/api/signing/{signing_token}/status.json")
        return {"ok": True, "status": (out or {}).get("status"),
                "signed_file_url": (out or {}).get("file")}
    except Exception as e:
        log.warning("dokobit.signing_status failed: %s", e)
        return {"ok": False, "error": f"status failed: {e}"}


def _is_dokobit_host(url):
    """True iff `url` is an https URL on the configured Gateway host. The access token is
    added to this request, so we must NEVER fetch (and leak the token to) an arbitrary
    host fed via a postback body. Defensive; any parse problem -> False."""
    try:
        from urllib.parse import urlparse
        u = urlparse(url or "")
        if u.scheme != "https" or not u.hostname:
            return False
        allowed = urlparse(_base()).hostname
        host = u.hostname.lower()
        return host == allowed or host.endswith(".dokobit.com")
    except Exception:
        return False


def fetch_signed(url):
    """Download the signed document bytes from a Dokobit file URL (the token is added as
    a param). Returns the bytes, or None on any failure / when the seam is OFF. Never
    raises and NEVER logs the token.

    SSRF GUARD: the access token rides this request as a query param, so the URL MUST be
    on the configured Dokobit Gateway host — a foreign host (e.g. fed via an untrusted
    postback body) is refused so the token is never leaked off-domain."""
    if not enabled():
        return None
    try:
        if not url:
            return None
        if not _is_dokobit_host(url):
            log.warning("dokobit.fetch_signed refused off-allowlist host")
            return None
        import requests
        r = requests.get(url, params={"access_token": _token()}, timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        return r.content
    except Exception as e:
        log.warning("dokobit.fetch_signed failed: %s", e)
        return None


def parse_postback(form):
    """Parse a Dokobit postback payload (a dict-like form / JSON body) into a normalized
    {"action","signing_token","status","file_url"}. Pure + defensive; never raises.
    `form` may be a Flask `request.form`, a plain dict, or JSON."""
    try:
        get = form.get if hasattr(form, "get") else (lambda k, d=None: None)
        # Dokobit names the signing token variously across actions; accept the common keys.
        token = (get("token") or get("signing_token") or get("signing") or "")
        return {
            "action": (get("action") or get("status") or "") or "",
            "signing_token": (token or "").strip(),
            "status": (get("status") or "") or "",
            "file_url": (get("file") or get("file_url") or "") or "",
        }
    except Exception as e:
        log.warning("dokobit.parse_postback failed: %s", e)
        return {"action": "", "signing_token": "", "status": "", "file_url": ""}


# ---------------------------------------------------------------- Identity Gateway (eID) STUB
def authenticate(*_args, **_kwargs):
    """STUB for the Dokobit Identity Gateway (eID — Smart-ID / Mobile-ID authentication),
    which is a SEPARATE service from the signing Gateway above. Intentionally NOT built in
    this seam: it always reports not-configured so a future caller has a documented entry
    point without any network behaviour today. Never raises."""
    return {"ok": False, "error": "Dokobit Identity Gateway (eID) is not configured"}
