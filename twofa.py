"""
TWO-FACTOR AUTHENTICATION — per-user OPT-IN email one-time codes (OTP) for the
local username/password login.

After a correct password, a user who has opted in receives a short-lived 6-digit
code by EMAIL and must enter it to finish signing in. This is a SECOND factor on
top of the password — it never replaces the password check, and it is DEFAULT-OFF:

  * a master admin switch `twofa_email_enabled` (auth app_settings) gates the whole
    feature — OFF (the default) means no challenge ever, login byte-identical to today;
  * a per-user opt-in flag `users.twofa_email` plus a non-empty `users.email` (the user
    must have enabled it through the verified-email flow in /account).

Only users who THEMSELVES opted in via the verified-email flow are ever challenged,
so a misconfigured global can't lock everyone out, and an admin can always disable a
user's 2FA (break-glass — see app.py /admin) if they lose email access.

The OTP itself lives in `login_otp` (security.db, auth.connect()): ONE pending code
per user (replaced on resend), stored HASHED (scrypt, per-code salt) — never plaintext.
A code is valid for OTP_TTL_SEC, single-use (deleted on success), capped at MAX_ATTEMPTS
wrong tries, and resends honour a RESEND_COOLDOWN_SEC cooldown.

Transport is INJECTABLE (like notify.py): send_code() resolves an SMTP transport from
admin settings, but tests inject a fake with .send(to, subject, html, text). Nothing
here ever logs the plaintext code.
"""
import os
import secrets
import time

import applog
import auth
import db_migrate

WORKDIR = os.path.dirname(os.path.abspath(__file__))
log = applog.get("twofa")

OTP_DIGITS = 6
OTP_TTL_SEC = 600           # a code is valid for 10 minutes
RESEND_COOLDOWN_SEC = 60    # min seconds between (re)sends to one user
MAX_ATTEMPTS = 5            # wrong tries before the code is invalidated
PENDING_2FA_TTL_SEC = 600   # absolute lifetime of the pending-2fa login state (app.py)

# scrypt params for the per-code hash — cheap relative to the password KDF (the code is
# short-lived and rate-limited) but still salted + memory-hard, never a bare digest.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024


def _ensure_schema(con):
    """Create the login_otp table (once per DB) via db_migrate. APPEND only."""
    db_migrate.apply(con, "twofa", [
        "CREATE TABLE IF NOT EXISTS login_otp ("
        "username TEXT PRIMARY KEY, code_hash TEXT, salt TEXT, "
        "expires_at REAL, attempts INTEGER DEFAULT 0, last_sent REAL, "
        "created_at REAL)",
    ])


def connect():
    """A security.db connection with the login_otp schema ensured. Reuses auth.connect()
    (same security.db, audit-bound) so the OTP store sits alongside the user store."""
    con = auth.connect()
    _ensure_schema(con)
    return con


def _hash(code, salt):
    """Salted scrypt hash of a code (hex string), for constant-time compare on verify."""
    import hashlib
    return hashlib.scrypt(code.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                          p=_SCRYPT_P, maxmem=_SCRYPT_MAXMEM).hex()


# ---------------------------------------------------------------- enablement helpers
def enabled():
    """Is the MASTER email-2FA switch ON? OFF (default) => no challenge anywhere."""
    return (auth.get_setting("twofa_email_enabled") or "").strip() in ("1", "true", "True", "on")


def twofa_enabled(username):
    """Does this user get a 2FA challenge on login? TRUE only when ALL hold:
      * the master switch is ON,
      * the user opted in (users.twofa_email = 1),
      * the user has a non-empty email (a code can actually be delivered).
    Never raises — any lookup failure returns False (fail toward NO challenge for the
    enablement check; the login route still fails CLOSED if a challenge can't be sent)."""
    try:
        if not username or not enabled():
            return False
        con = auth.connect()
        row = con.execute("SELECT twofa_email, email FROM users WHERE username=? AND active=1",
                          (username,)).fetchone()
        con.close()
        if not row:
            return False
        return bool(row["twofa_email"]) and bool((row["email"] or "").strip())
    except Exception as e:
        log.warning("twofa_enabled lookup failed for %r: %s", username, e)
        return False


# ---------------------------------------------------------------- code lifecycle
def _now():
    return time.time()


def issue(username):
    """Generate and store a fresh OTP for `username`, returning the PLAINTEXT code so the
    caller can email it (the caller NEVER logs it; this module never persists/logs it).

    Stores the code HASHED with a fresh per-code salt, sets expires_at = now + OTP_TTL_SEC,
    resets attempts to 0, stamps last_sent. ONE pending code per user — a resend REPLACES
    the row. Honours a RESEND_COOLDOWN_SEC cooldown: if the existing code was sent within
    the cooldown, returns None (the caller shows "please wait / code already sent") and
    leaves the existing code intact. Returns None on any unexpected error (caller treats
    as "could not issue")."""
    try:
        if not username:
            return None
        con = connect()
        existing = con.execute("SELECT last_sent FROM login_otp WHERE username=?",
                               (username,)).fetchone()
        now = _now()
        if existing and existing["last_sent"] and (now - existing["last_sent"]) < RESEND_COOLDOWN_SEC:
            con.close()
            return None   # cooldown — keep the existing code, tell the caller to wait
        code = "".join(str(secrets.randbelow(10)) for _ in range(OTP_DIGITS))
        salt = secrets.token_bytes(16)
        con.execute(
            "INSERT INTO login_otp (username, code_hash, salt, expires_at, attempts, "
            "last_sent, created_at) VALUES (?,?,?,?,0,?,?) "
            "ON CONFLICT(username) DO UPDATE SET code_hash=excluded.code_hash, "
            "salt=excluded.salt, expires_at=excluded.expires_at, attempts=0, "
            "last_sent=excluded.last_sent, created_at=excluded.created_at",
            (username, _hash(code, salt), salt.hex(), now + OTP_TTL_SEC, now, now))
        con.commit(); con.close()
        return code
    except Exception as e:
        log.warning("issue failed for %r: %s", username, e)
        return None


def verify(username, code):
    """Check a submitted code against the pending OTP. Returns
    {"ok": bool, "reason": str}. Constant-time compare against the stored hash.

    Rejects (ok=False) and the reason:
      * "no_code"  — no pending code for the user;
      * "expired"  — past expires_at (the row is invalidated);
      * "locked"   — attempts already >= MAX_ATTEMPTS (row invalidated);
      * "bad_code" — wrong code (attempts incremented; row invalidated once the cap is hit).
    On success the row is DELETED (single-use) and {"ok": True} is returned. Never raises —
    an unexpected error returns ok=False, reason="error" (fail closed)."""
    try:
        if not username or code is None:
            return {"ok": False, "reason": "no_code"}
        con = connect()
        row = con.execute(
            "SELECT code_hash, salt, expires_at, attempts FROM login_otp WHERE username=?",
            (username,)).fetchone()
        if not row:
            con.close()
            return {"ok": False, "reason": "no_code"}
        if (row["attempts"] or 0) >= MAX_ATTEMPTS:
            con.execute("DELETE FROM login_otp WHERE username=?", (username,))
            con.commit(); con.close()
            return {"ok": False, "reason": "locked"}
        if not row["expires_at"] or _now() > row["expires_at"]:
            con.execute("DELETE FROM login_otp WHERE username=?", (username,))
            con.commit(); con.close()
            return {"ok": False, "reason": "expired"}
        salt = bytes.fromhex(row["salt"])
        want = row["code_hash"] or ""
        got = _hash(str(code).strip(), salt)
        if secrets.compare_digest(got, want):
            con.execute("DELETE FROM login_otp WHERE username=?", (username,))
            con.commit(); con.close()
            return {"ok": True, "reason": "ok"}
        # wrong code: increment attempts; invalidate once the cap is reached.
        attempts = (row["attempts"] or 0) + 1
        if attempts >= MAX_ATTEMPTS:
            con.execute("DELETE FROM login_otp WHERE username=?", (username,))
        else:
            con.execute("UPDATE login_otp SET attempts=? WHERE username=?",
                        (attempts, username))
        con.commit(); con.close()
        return {"ok": False, "reason": "bad_code"}
    except Exception as e:
        log.warning("verify failed for %r: %s", username, e)
        return {"ok": False, "reason": "error"}


def attempts_left(username):
    """How many wrong tries remain on the pending code (for the UI), or 0 if none/dead."""
    try:
        con = connect()
        row = con.execute("SELECT attempts FROM login_otp WHERE username=?",
                          (username,)).fetchone()
        con.close()
        if not row:
            return 0
        return max(0, MAX_ATTEMPTS - (row["attempts"] or 0))
    except Exception as e:
        log.warning("attempts_left failed for %r: %s", username, e)
        return 0


def clear(username):
    """Delete any pending OTP for the user (admin break-glass / opt-out cleanup). Best
    effort — never raises."""
    try:
        con = connect()
        con.execute("DELETE FROM login_otp WHERE username=?", (username,))
        con.commit(); con.close()
    except Exception as e:
        log.warning("clear failed for %r: %s", username, e)


# ---------------------------------------------------------------- send
def send_code(username, code, transport=None):
    """Email the 6-digit `code` to the user's address. Best-effort, NEVER raises; returns
    True only when a message actually went out. `transport` (with .send(to, subject, html,
    text)) is injected by tests; in production it is built from admin SMTP settings (the
    SAME settings notify.py uses). The plaintext code is in the email body ONLY — it is
    never logged here. A False return means the caller must fail the login closed."""
    try:
        if not code:
            return False
        to = auth.user_email(username)
        if not to:
            log.info("send_code: no email for %r — cannot deliver", username)
            return False
        if transport is None:
            import notify
            transport = notify._settings_transport()
        if transport is None:
            log.info("send_code: no SMTP host configured (smtp_host) — cannot deliver")
            return False
        subject = "Your Fleet Fuel sign-in code"
        text = (f"{code}\n\n"
                "This is your Fleet Fuel sign-in code. It is valid for 10 minutes. "
                "Do not share it with anyone.")
        html = (f'<p style="font-size:24px;font-weight:700;letter-spacing:3px">{code}</p>'
                "<p>This is your Fleet Fuel sign-in code. It is valid for 10 minutes. "
                "Do not share it with anyone.</p>")
        transport.send(to, subject, html, text)
        log.info("send_code: sign-in code sent to user %r", username)
        return True
    except Exception as e:
        log.warning("send_code failed for %r: %s", username, e)
        return False


def mask_email(email):
    """Mask an email for display on the verify page, e.g. a***@domain.com. Best-effort —
    returns '' for a falsy/odd value rather than raising."""
    try:
        email = (email or "").strip()
        if "@" not in email:
            return ""
        local, _, domain = email.partition("@")
        if not local:
            return "***@" + domain
        return local[0] + "***@" + domain
    except Exception:
        return ""
