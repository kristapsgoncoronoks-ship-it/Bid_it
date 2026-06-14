"""
API KEY MODEL - admin-issued bearer tokens for the versioned /api/v1/* contract.

SECURITY DISCIPLINE (mirrors the password store in auth.py):
  * a token is high-entropy (secrets.token_urlsafe(32) -> 256 bits) and is shown
    to the admin EXACTLY ONCE, at issuance; we never persist the plaintext.
  * only the SHA-256 hash of the token is stored, in a BLOB column. The audit layer
    excludes BLOB columns from its JSON snapshots (audit.py _cols_pk), so the hash
    never leaks into the change log either.
  * verification re-hashes the presented token and compares CONSTANT-TIME
    (secrets.compare_digest) against every active key's stored hash, so a wrong
    token can't be distinguished by timing.
  * DEFAULT OFF: no rows exist until an admin issues a key, so the /api/v1 surface
    is inert (every request -> 401) on a fresh install.

The table lives in security.db alongside users (auth.connect()); issuance and
revocation are audit-logged via the api_keys trigger (actor set by the caller).
Per-call usage is metered into api_usage so per-key usage is queryable.

A key carries a SET of capability SCOPES (api:benchmark / api:claims / api:savings).
The token-auth path resolves the key, then the per-endpoint guard checks the scope.
"""
import os, sqlite3, hashlib, secrets, time
import auth
import audit
import db_migrate

# Scopes a key may hold. Each /api/v1 endpoint requires exactly one of these.
SCOPES = {
    "api:benchmark": "Read the internal price benchmark summary",
    "api:claims":    "Read VAT claim status / readiness (non-sensitive fields only)",
    "api:savings":   "Read the savings-intelligence summary",
    "api:crm":       "Read customer master data (list + detail) for CRM sync",
    "api:crm.write": "Create / update customer master data (CRM sync write)",
}

_SCHEMA_READY = set()


def connect():
    """A security.db handle with the api_keys schema/migrations applied and the
    api_keys + api_usage tables registered for audit. Reuses auth.connect() so the
    same WAL/tuning/actor binding applies; key-table writes are audit-logged."""
    con = auth.connect()
    dbfile = auth.DB
    if dbfile == ":memory:" or dbfile not in _SCHEMA_READY:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT,
            token_sha256 BLOB,           -- SHA-256 of the plaintext token (never the token)
            scopes TEXT,                 -- space-separated scope set
            owner TEXT,                  -- the admin who issued the key
            created TEXT DEFAULT CURRENT_TIMESTAMP,
            last_used TEXT,
            revoked INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT CURRENT_TIMESTAMP,
            key_id INTEGER,
            endpoint TEXT,
            status INTEGER);
        CREATE INDEX IF NOT EXISTS ix_api_usage_key ON api_usage(key_id, ts);
        """)
        db_migrate.apply(con, "api_keys", [])  # reserve the migration channel (append-only)
        # api_keys is change-logged (issuance/revocation); api_usage is high-volume
        # metering, not audited. The BLOB token hash is excluded from snapshots.
        audit.install_audit(con, ["api_keys"])
        con.commit()
        _SCHEMA_READY.add(dbfile)
    return con


def _hash(token):
    """SHA-256 of the presented/issued token. Tokens are already 256-bit random, so
    a fast cryptographic hash is the right primitive here (unlike passwords, which
    need a slow KDF because they are low-entropy)."""
    return hashlib.sha256(token.encode()).digest()


def _norm_scopes(scopes):
    out = []
    for s in scopes or []:
        s = s.strip()
        if s in SCOPES and s not in out:
            out.append(s)
    return out


def issue(label, scopes, owner):
    """Create a key. Returns (id, plaintext_token). The plaintext is returned ONCE
    and never stored — only its SHA-256 hash is persisted. `owner` is the issuing
    admin (recorded for accountability)."""
    norm = _norm_scopes(scopes)
    if not norm:
        raise ValueError("a key must have at least one valid scope")
    token = secrets.token_urlsafe(32)            # 256 bits of entropy
    con = connect()
    cur = con.execute(
        "INSERT INTO api_keys (label, token_sha256, scopes, owner) VALUES (?,?,?,?)",
        ((label or "").strip()[:120], _hash(token), " ".join(norm), owner or ""))
    kid = cur.lastrowid
    con.commit(); con.close()
    return kid, token


def revoke(key_id):
    """Revoke a key immediately. A revoked key fails the token check (401) on its
    very next call — the verifier filters revoked=0."""
    con = connect()
    con.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (int(key_id),))
    con.commit(); con.close()


def list_keys():
    """All keys (never the token, which we don't have) for the admin UI."""
    con = connect()
    rows = [dict(r) for r in con.execute(
        "SELECT id, label, scopes, owner, created, last_used, revoked "
        "FROM api_keys ORDER BY id DESC")]
    con.close()
    return rows


def verify(token):
    """Resolve a presented token to its active key row (as a dict) or None.

    Compares the token's hash CONSTANT-TIME against every active (revoked=0) key.
    Returns None for a missing/blank/unknown/revoked token. On success bumps
    last_used. The caller then enforces the per-endpoint scope."""
    if not token:
        return None
    h = _hash(token)
    con = connect()
    match = None
    for r in con.execute(
            "SELECT id, label, scopes, owner, created, last_used, revoked "
            "FROM api_keys WHERE revoked=0"):
        stored = con.execute("SELECT token_sha256 FROM api_keys WHERE id=?",
                             (r["id"],)).fetchone()["token_sha256"]
        # constant-time compare; keep scanning so timing doesn't reveal a match
        if stored is not None and secrets.compare_digest(stored, h):
            match = dict(r)
    if match is not None:
        con.execute("UPDATE api_keys SET last_used=CURRENT_TIMESTAMP WHERE id=?",
                    (match["id"],))
        con.commit()
        match["scopes"] = set((match["scopes"] or "").split())
    con.close()
    return match


def has_scope(key, scope):
    return bool(key) and scope in key.get("scopes", set())


def log_usage(key_id, endpoint, status):
    """Meter one /api/v1 call so per-key usage is queryable. Best-effort: metering
    must never break a request, but the failure is surfaced to the app log."""
    try:
        con = connect()
        con.execute("INSERT INTO api_usage (key_id, endpoint, status) VALUES (?,?,?)",
                    (key_id, endpoint, int(status)))
        con.commit(); con.close()
    except Exception:
        import applog
        applog.get("api_keys").exception("api_usage logging failed")


def usage_summary():
    """Per-key call counts + last call, for the admin UI."""
    con = connect()
    rows = {r["key_id"]: dict(r) for r in con.execute(
        "SELECT key_id, COUNT(*) AS calls, MAX(ts) AS last_call "
        "FROM api_usage GROUP BY key_id")}
    con.close()
    return rows
