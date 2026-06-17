"""
appsecrets.py — admin-managed secrets that belong in the process ENVIRONMENT.

Lets an admin set provider API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, the Azure
trio) from the Admin panel instead of hand-editing a systemd drop-in / shell profile.
Every value is sealed at rest with keyvault ENVELOPE encryption (a fresh AES-256-GCM
DEK per secret, wrapped by the KEK) and stored in `app_settings` under
`envsecret_<NAME>` (base64 of the sealed blob). `security.db` is already gitignored.

`load_into_environ()` decrypts the stored secrets into `os.environ` at startup so the
existing `os.environ["ANTHROPIC_API_KEY"]` reads across the codebase (ai_review,
ai_verify, ai_assistant, extract) keep working UNCHANGED — but it NEVER overrides a
value already present in the real environment, so an ops-managed systemd
`Environment=`/`EnvironmentFile=` key always WINS over the panel-stored one.

The plaintext is never returned to the UI: `status()` reports only the effective
SOURCE (env / stored / none) plus a short masked tail (last 4 chars). Saving an EMPTY
value CLEARS the stored secret. Nothing here ever logs a plaintext key.
"""
import base64
import os

import auth
import keyvault
import applog

log = applog.get("appsecrets")

PREFIX = "envsecret_"

# The env-var names an admin may manage from the panel. Only these are accepted —
# an unknown name is rejected so the panel can't write arbitrary environment.
MANAGED = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AZURE_OPENAI_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
)

# Endpoint/deployment are not real "secrets" but ride the same store for convenience;
# they are not masked as aggressively in the UI hint.
_NOT_SECRET = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT")


def _aad(name):
    """Bind the sealed blob to its variable name so a blob can't be replayed as another."""
    return "envsecret:" + name


def _stored_blob(name):
    raw = auth.get_setting(PREFIX + name, "") or ""
    if not raw:
        return None
    try:
        return base64.b64decode(raw.encode("ascii"))
    except Exception:
        return None


def is_stored(name):
    return _stored_blob(name) is not None


def set_secret(name, value):
    """Seal+store a managed secret (or CLEAR it when value is blank), and apply it to
    os.environ immediately so it takes effect WITHOUT a restart. A real env var set by
    systemd is left untouched in os.environ here, but the stored value is what
    `load_into_environ()` would use on the next boot only if the env var is absent."""
    name = (name or "").strip()
    if name not in MANAGED:
        raise ValueError("unmanaged secret name: %r" % name)
    value = (value or "").strip()
    if not value:
        # clear the stored copy; leave os.environ alone (can't tell ours from systemd's)
        auth.set_setting(PREFIX + name, "")
        return "cleared"
    blob = keyvault.seal(value, _aad(name))
    auth.set_setting(PREFIX + name, base64.b64encode(blob).decode("ascii"))
    os.environ[name] = value   # effective now, no restart needed
    return "saved"


def clear_secret(name):
    return set_secret(name, "")


def load_into_environ():
    """Startup hook: decrypt every stored managed secret into os.environ, WITHOUT
    overriding a value already set in the real environment (systemd/ops wins). Never
    raises — a bad/undecryptable blob is logged and skipped so the app still boots."""
    loaded = 0
    for name in MANAGED:
        if os.environ.get(name):       # real env var present -> it wins, leave it
            continue
        blob = _stored_blob(name)
        if not blob:
            continue
        try:
            os.environ[name] = keyvault.open(blob, _aad(name))
            loaded += 1
        except Exception as e:
            log.warning("could not load stored secret %s into environ: %s", name, e)
    return loaded


def _mask(name, v):
    v = v or ""
    if not v:
        return ""
    if name in _NOT_SECRET:
        return v          # endpoints/deployments aren't secret
    return ("…" + v[-4:]) if len(v) >= 4 else "set"


def status(name):
    """Report (source, hint) for the panel.

    source: 'env'    — provided by the real environment (systemd/shell); panel cannot
                       override it, and it takes precedence over any stored value;
            'stored' — set from the panel (sealed at rest), no overriding env var;
            'none'   — not configured anywhere.
    hint:   short masked tail for display (never the full secret)."""
    stored = _stored_blob(name)
    env_v = os.environ.get(name)
    if env_v and not stored:
        return ("env", _mask(name, env_v))
    if stored:
        # If a real env var also exists, load_into_environ leaves it in place (env wins);
        # but the admin set a stored copy, so report 'stored' with a note handled by UI.
        try:
            return ("stored", _mask(name, keyvault.open(stored, _aad(name))))
        except Exception:
            return ("stored", "??")
    if env_v:
        return ("env", _mask(name, env_v))
    return ("none", "")
