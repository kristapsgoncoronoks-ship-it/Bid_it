import sqlite3
"""
AUTHENTICATION - local user store for the web UI (security.db).

Passwords are hashed with scrypt (salted, never stored in plain text). Login
attempts (success and failure) are recorded in login_log. The logged-in
username flows into every database change via the audit layer (changed_by).

CLI:
    python3 auth.py add <username>        prompts for password (or pass as 3rd arg)
    python3 auth.py disable <username>
    python3 auth.py list
"""
import os, sqlite3, hashlib, secrets, sys, time
import audit
import db_tuning
import db_migrate
import applog

log = applog.get("auth")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/security.db"

ROLES = ("processor", "admin")
# admin:     full control, including server setup, certificates, and user /
#            permission administration ("overall software changes").
# processor: day-to-day operations. Capabilities are admin-configurable, but a
#            processor can NEVER do server setup or user/permission admin.

# Grantable capabilities — an admin can toggle each of these for the processor
# role from the Admin panel.
PERMISSIONS = {
    "data_import":     "Import batches & edit transaction data",
    "invoice_control": "Register & triage supplier statements",
    "vat_claims":      "Manage VAT refund claims & status",
    "customers":       "Onboard & manage VAT-refund customers (docs, fees, activation)",
    "pricing":         "Upload pricing & wholesale data",
    "documents":       "Attach & manage invoice documents",
    "share":           "Create & manage secure public share links",
    "exports":         "Download Excel / data exports",
}
# Admin-only capabilities — the "server setup & overall software changes"
# boundary. These are never grantable to a processor.
ADMIN_ONLY = {
    "user_admin":   "Manage users & adjust Processor permissions",
    "system_setup": "Server setup, certificates & configuration",
}
# Legacy role names map onto the processor capability set for backward compat.
_LEGACY = {"viewer": "processor", "editor": "processor"}

# scrypt cost. Legacy hashes were n=2**14; new/upgraded hashes use NEW_N.
LEGACY_N = 2 ** 14          # 16384
NEW_N = 2 ** 16             # 65536
SCRYPT_R, SCRYPT_P = 8, 1
# maxmem must cover 128 * n * r bytes (+ overhead). For NEW_N: 128*65536*8 ~= 64MiB.
SCRYPT_MAXMEM = 132 * 1024 * 1024

_SCHEMA_READY = set()   # DB files whose schema is set up this process

def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)  # WAL + busy_timeout for safe multi-process access
    audit.bind(con)   # audit triggers call ffs_actor(); register it every connect
    # Schema/migrations/seeding persist in the file; run once per process per DB
    # (connect() is called on every page render via permissions_for).
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, salt BLOB, pw_hash BLOB,
            active INTEGER DEFAULT 1, created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS login_log (
            ts TEXT DEFAULT CURRENT_TIMESTAMP, username TEXT, success INTEGER, remote TEXT);
        CREATE TABLE IF NOT EXISTS error_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT CURRENT_TIMESTAMP,
            username TEXT, context TEXT, etype TEXT, message TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY, value TEXT);
        """)
        # versioned migrations: run once per database (db_migrate). Append only.
        # (kdf_n: per-user scrypt cost; existing hashes keep their legacy parameters.)
        # tenancy is imported LAZILY here: tenancy.connect() reuses auth.connect(),
        # so a top-level `import tenancy` in auth would be a circular import. The
        # tenant_id columns on the per-tenant platform tables (error_log/login_log)
        # are PURE schema plumbing (P1) — nothing reads/filters them yet.
        import tenancy
        db_migrate.apply(con, "auth", [
            "ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'editor'",
            f"ALTER TABLE users ADD COLUMN kdf_n INTEGER DEFAULT {LEGACY_N}",
            *tenancy.tenant_column_ddls(["error_log", "login_log"]),
        ])
        _seed_permissions(con)
        audit.install_audit(con, ["users", "role_permissions"])  # both change-logged
        con.commit()
        try: os.chmod(DB, 0o600)
        except OSError as e:
            log.warning("connect: could not restrict permissions on secrets DB %s: %s", DB, e)
        _SCHEMA_READY.add(DB)
    return con

def _seed_permissions(con):
    """Ensure the role_permissions table exists and the processor role has a row
    for every grantable capability (default: granted)."""
    con.execute("""CREATE TABLE IF NOT EXISTS role_permissions (
        role TEXT, perm TEXT, allowed INTEGER DEFAULT 1,
        PRIMARY KEY (role, perm))""")
    have = {r["perm"] for r in con.execute(
        "SELECT perm FROM role_permissions WHERE role='processor'")}
    for perm in PERMISSIONS:
        if perm not in have:
            con.execute("INSERT INTO role_permissions (role, perm, allowed) "
                        "VALUES ('processor', ?, 1)", (perm,))

def permissions_for(role):
    """Set of capability keys the role currently holds."""
    if role == "admin":
        return set(PERMISSIONS) | set(ADMIN_ONLY)
    role = _LEGACY.get(role, role)
    con = connect()
    perms = {r["perm"] for r in con.execute(
        "SELECT perm FROM role_permissions WHERE role=? AND allowed=1", (role,))}
    con.close()
    return perms

def has_perm(role, perm):
    if role == "admin":
        return True
    return perm in permissions_for(role)

def get_role_permissions(role="processor"):
    """perm -> bool map for the Admin UI (defaults to granted if unset)."""
    role = _LEGACY.get(role, role)
    con = connect()
    rows = {r["perm"]: bool(r["allowed"]) for r in con.execute(
        "SELECT perm, allowed FROM role_permissions WHERE role=?", (role,))}
    con.close()
    return {perm: rows.get(perm, True) for perm in PERMISSIONS}

def set_permission(role, perm, allowed):
    assert role == "processor", "only the processor role's permissions are configurable"
    assert perm in PERMISSIONS, f"unknown permission {perm}"
    con = connect()
    con.execute("""INSERT INTO role_permissions (role, perm, allowed) VALUES (?,?,?)
                   ON CONFLICT(role, perm) DO UPDATE SET allowed=excluded.allowed""",
                (role, perm, int(bool(allowed))))
    con.commit(); con.close()

# ---------------------------------------------------------------- error log
ERROR_LOG_KEEP = 2000     # cap so security.db can't grow without bound

def log_error(context, etype, message, detail="", user=""):
    """Record an application error for the Admin panel. Best-effort: logging must
    never raise inside an error path, so all failures here are swallowed. The table
    is capped to the most recent ERROR_LOG_KEEP rows."""
    try:
        con = connect()
        con.execute("""INSERT INTO error_log (username, context, etype, message, detail)
                       VALUES (?,?,?,?,?)""",
                    (user or "", (context or "")[:200], (etype or "")[:80],
                     (message or "")[:1000], (detail or "")[:8000]))
        # prune anything beyond the cap (cheap: id is the PK, so this is indexed)
        con.execute("DELETE FROM error_log WHERE id <= "
                    "(SELECT MAX(id) FROM error_log) - ?", (ERROR_LOG_KEEP,))
        con.commit(); con.close()
    except Exception:
        pass

def recent_errors(limit=200):
    con = connect()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM error_log ORDER BY id DESC LIMIT ?", (int(limit),))]
    con.close()
    # SELECT * surfaces a column-set contract to the admin view; drop the inert
    # P1 tenant_id plumbing column so it isn't part of the surfaced dict (P2 will
    # scope this read, never expose the raw column).
    import tenancy
    for r in rows:
        r.pop(tenancy.TENANT_COLUMN, None)
    return rows

def clear_errors():
    con = connect()
    con.execute("DELETE FROM error_log")
    con.commit(); con.close()

# ---------------------------------------------------------------- settings
# ---------------------------------------------------------------- brute-force lockout
LOGIN_MAX_FAILS = 8        # consecutive failures (since last success) before lockout
LOGIN_WINDOW_MIN = 15      # within this many minutes

def recent_failures(username):
    """Failed logins for a username since the last success, within the window."""
    con = connect()
    n = con.execute("""SELECT COUNT(*) FROM login_log WHERE username=? AND success=0
        AND ts >= datetime('now', ?)
        AND ts > COALESCE((SELECT MAX(ts) FROM login_log WHERE username=? AND success=1),
                          '1970-01-01')""",
        (username, f"-{LOGIN_WINDOW_MIN} minutes", username)).fetchone()[0]
    con.close()
    return n

def is_locked(username):
    return bool(username) and recent_failures(username) >= LOGIN_MAX_FAILS

LOGIN_IP_MAX_FAILS = 25    # failures from one source IP within the window -> throttle

def is_locked_ip(remote):
    """Throttle a source IP spraying many usernames (botnet brute force)."""
    if not remote:
        return False
    con = connect()
    n = con.execute("""SELECT COUNT(*) FROM login_log WHERE remote=? AND success=0
        AND ts >= datetime('now', ?)""", (remote, f"-{LOGIN_WINDOW_MIN} minutes")).fetchone()[0]
    con.close()
    return n >= LOGIN_IP_MAX_FAILS

def get_setting(key, default=None):
    con = connect()
    row = con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    con.close()
    return row["value"] if row else default

def set_setting(key, value):
    con = connect()
    con.execute("""INSERT INTO app_settings (key, value) VALUES (?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, str(value)))
    con.commit(); con.close()

def _hash(password, salt, n=NEW_N, maxmem=SCRYPT_MAXMEM):
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=SCRYPT_R, p=SCRYPT_P,
                          maxmem=maxmem)

def add_user(username, password, role="processor"):
    assert role in ROLES, f"role must be one of {ROLES}"
    con = connect()
    salt = secrets.token_bytes(16)
    prev = con.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
    # a password reset (role left at the default) must not change an existing role
    keep_role = prev["role"] if prev and role == "processor" else role
    con.execute("""INSERT OR REPLACE INTO users (username, salt, pw_hash, active, role, kdf_n)
                   VALUES (?,?,?,1,?,?)""",
                (username, salt, _hash(password, salt, NEW_N), keep_role, NEW_N))
    con.commit(); con.close()

def set_role(username, role):
    assert role in ROLES
    con = connect()
    con.execute("UPDATE users SET role=? WHERE username=?", (role, username))
    con.commit(); con.close()

def set_active(username, active):
    con = connect()
    con.execute("UPDATE users SET active=? WHERE username=?", (int(active), username))
    con.commit(); con.close()

def get_user(username):
    con = connect()
    u = con.execute("SELECT username, role, active, created FROM users WHERE username=?",
                    (username,)).fetchone()
    con.close()
    return dict(u) if u else None

def list_users():
    con = connect()
    users = [dict(u) for u in con.execute(
        "SELECT username, role, active, created FROM users ORDER BY username")]
    for u in users:
        last = con.execute("""SELECT ts FROM login_log WHERE username=? AND success=1
                              ORDER BY ts DESC LIMIT 1""", (u["username"],)).fetchone()
        u["last_login"] = last["ts"] if last else "-"
    import tenancy
    logins = [dict(l) for l in con.execute(
        "SELECT * FROM login_log ORDER BY ts DESC LIMIT 25")]
    for l in logins:                       # keep the inert tenant_id out of the
        l.pop(tenancy.TENANT_COLUMN, None)  # surfaced contract (symmetry w/ recent_errors)
    con.close()
    return users, logins

def verify(username, password, remote=""):
    con = connect()
    u = con.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
    if u is not None:
        n = u["kdf_n"] or LEGACY_N
        # maxmem must be large enough for whichever n this user was hashed with.
        mm = SCRYPT_MAXMEM if n >= NEW_N else 132 * 1024 * 1024
        ok = secrets.compare_digest(_hash(password, u["salt"], n, mm), u["pw_hash"])
    else:
        # Unknown user: still run a full scrypt against a dummy salt so login time
        # does not reveal whether the username exists (timing oracle).
        _hash(password, b"\x00" * 16, NEW_N, SCRYPT_MAXMEM)
        ok = False
    con.execute("INSERT INTO login_log (username, success, remote) VALUES (?,?,?)",
                (username, int(ok), remote))
    # cap the login history so it can't grow without bound
    con.execute("DELETE FROM login_log WHERE rowid <= "
                "(SELECT MAX(rowid) FROM login_log) - 1000")
    # Transparent upgrade: on a successful login with a below-target cost, re-hash
    # the password at the new n and persist it.
    if ok and (u["kdf_n"] or LEGACY_N) < NEW_N:
        new_salt = secrets.token_bytes(16)
        con.execute("UPDATE users SET salt=?, pw_hash=?, kdf_n=? WHERE username=?",
                    (new_salt, _hash(password, new_salt, NEW_N), NEW_N, username))
    con.commit(); con.close()
    if not ok:
        time.sleep(1.0)   # slow down brute force
    return ok

def secret_key():
    """Persistent Flask session key shared by ALL worker processes (so sessions
    and CSRF tokens validate across processes), file mode 0600, generated once.
    Uses an atomic O_EXCL create so two processes starting at the same time on a
    fresh install can't generate two different keys (which would invalidate each
    other's sessions) — the loser simply reads the winner's key.

    HORIZONTAL SCALE: behind a load balancer, set FFS_SECRET_KEY to the SAME value
    on every web node. Sessions are signed cookies (no server-side store), so any
    node validates any node's cookie — NO sticky sessions needed. When the env var
    is unset we fall back to the per-machine .secret_key file (single-server default)."""
    env = os.environ.get("FFS_SECRET_KEY")
    if env:
        return env.encode() if isinstance(env, str) else env
    path = f"{WORKDIR}/.secret_key"
    if not os.path.exists(path):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, secrets.token_bytes(32))
            finally:
                os.close(fd)
        except FileExistsError:
            pass  # another process created it first — read it below
    return open(path, "rb").read()

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "add":
        import getpass
        pw = sys.argv[3] if len(sys.argv) > 3 else getpass.getpass(f"Password for {sys.argv[2]}: ")
        role = sys.argv[4] if len(sys.argv) > 4 else "processor"
        add_user(sys.argv[2], pw, role)
        print(f"user '{sys.argv[2]}' created/updated (role: {role})")
    elif cmd == "disable":
        con = connect(); con.execute("UPDATE users SET active=0 WHERE username=?", (sys.argv[2],))
        con.commit(); con.close(); print(f"user '{sys.argv[2]}' disabled")
    else:
        con = connect()
        for u in con.execute("SELECT username, role, active, created FROM users"):
            print(f"  {u['username']:20} {u['role']:8} {'active' if u['active'] else 'DISABLED':9} since {u['created']}")
        for l in con.execute("SELECT * FROM login_log ORDER BY ts DESC LIMIT 5"):
            print(f"  login {l['ts']} {l['username']:16} {'OK' if l['success'] else 'FAIL'}")
        con.close()
