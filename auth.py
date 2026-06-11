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

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = f"{WORKDIR}/security.db"

ROLES = ("viewer", "editor", "admin")
# viewer: read-only (all GET pages); editor: + all operational changes (POST);
# admin: + user management panel (/admin)

def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY, salt BLOB, pw_hash BLOB,
        active INTEGER DEFAULT 1, created TEXT DEFAULT (datetime('now')));
    CREATE TABLE IF NOT EXISTS login_log (
        ts TEXT DEFAULT (datetime('now')), username TEXT, success INTEGER, remote TEXT);
    """)
    try: con.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'editor'")
    except sqlite3.OperationalError: pass  # column already exists (safe)
    audit.install_audit(con, ["users"])   # user management is change-logged too
    try: os.chmod(DB, 0o600)
    except OSError: pass
    return con

def _hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)

def add_user(username, password, role="editor"):
    assert role in ROLES, f"role must be one of {ROLES}"
    con = connect()
    salt = secrets.token_bytes(16)
    prev = con.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
    keep_role = prev["role"] if prev and role == "editor" else role
    con.execute("""INSERT OR REPLACE INTO users (username, salt, pw_hash, active, role)
                   VALUES (?,?,?,1,?)""",
                (username, salt, _hash(password, salt), keep_role))
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
    logins = [dict(l) for l in con.execute(
        "SELECT * FROM login_log ORDER BY ts DESC LIMIT 25")]
    con.close()
    return users, logins

def verify(username, password, remote=""):
    con = connect()
    u = con.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
    ok = bool(u) and secrets.compare_digest(_hash(password, u["salt"]), u["pw_hash"])
    con.execute("INSERT INTO login_log (username, success, remote) VALUES (?,?,?)",
                (username, int(ok), remote))
    con.commit(); con.close()
    if not ok:
        time.sleep(1.0)   # slow down brute force
    return ok

def secret_key():
    """Persistent Flask session key, file mode 0600, generated once."""
    path = f"{WORKDIR}/.secret_key"
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(secrets.token_bytes(32))
        os.chmod(path, 0o600)
    return open(path, "rb").read()

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "add":
        import getpass
        pw = sys.argv[3] if len(sys.argv) > 3 else getpass.getpass(f"Password for {sys.argv[2]}: ")
        role = sys.argv[4] if len(sys.argv) > 4 else "editor"
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
