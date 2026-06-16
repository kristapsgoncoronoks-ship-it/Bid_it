"""
SETUP WIZARD - friendly, guided installation. Run once after unzipping:

    Linux/macOS:   ./install.sh        (or: python3 setup_wizard.py)
    Windows:       double-click install.bat   (or: python setup_wizard.py)

It checks Python, installs dependencies, creates your HTTPS certificate, creates
the admin account, secures file permissions, runs a self-check, and tells you the
exact address to open. Safe to re-run any time (it skips what's already done).

Unattended mode (for IT automation):
    python3 setup_wizard.py --yes --user admin --password 'S3cret!' --host fuel.local
"""
import os, sys, subprocess, getpass, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
GREEN, RED, BOLD, END = "\033[92m", "\033[91m", "\033[1m", "\033[0m"
if os.name == "nt":
    GREEN = RED = BOLD = END = ""

def ok(msg):   print(f"  {GREEN}[OK]{END} {msg}")
def fail(msg): print(f"  {RED}[!!]{END} {msg}")
def step(n, msg): print(f"\n{BOLD}Step {n}: {msg}{END}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="no questions (unattended)")
    ap.add_argument("--user", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--skip-deps", action="store_true")
    a = ap.parse_args()

    print(f"{BOLD}=== Fleet Fuel & VAT Refund System - Setup ==={END}")

    # ---- 1. Python version -------------------------------------------------
    step(1, "Checking Python")
    if sys.version_info < (3, 10):
        fail(f"Python {sys.version.split()[0]} found - need 3.10+. "
             "Install from python.org and re-run.")
        sys.exit(1)
    ok(f"Python {sys.version.split()[0]}")

    # ---- 2. Dependencies ---------------------------------------------------
    step(2, "Installing required packages (flask, openpyxl, requests, cryptography)")
    if a.skip_deps:
        ok("skipped on request")
    else:
        pkgs = ["flask", "openpyxl", "requests", "cryptography"]
        for extra in ([], ["--break-system-packages"], ["--user"]):
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pkgs, *extra],
                               capture_output=True, text=True)
            if r.returncode == 0:
                ok("packages installed" + (f" ({' '.join(extra)})" if extra else ""))
                break
        else:
            fail("pip install failed - run manually: pip install " + " ".join(pkgs))
            sys.exit(1)

    # ---- 3. HTTPS certificate ----------------------------------------------
    step(3, "HTTPS certificate")
    sys.path.insert(0, HERE)
    import tls
    r = tls.resolve()
    if r:
        ok(f"already configured: {r[3]}")
    else:
        host = a.host
        if not a.yes:
            host = input("  Server name for the certificate [localhost]: ").strip() or "localhost"
        rc = subprocess.run([sys.executable, "make_cert.py", host],
                            capture_output=True, text=True)
        if rc.returncode == 0:
            ok(f"self-signed certificate created for '{host}' "
               "(browser warns once - choose 'Advanced -> Proceed'). "
               "A corporate/commercial cert can replace it later - see SECURITY.md.")
        else:
            fail("OpenSSL not found - the app will run on plain HTTP (localhost only). "
                 "Install OpenSSL and re-run, or set TLS_CERT/TLS_KEY later.")

    # ---- 4. Admin account ---------------------------------------------------
    step(4, "Administrator account")
    import auth
    con = auth.connect()
    admins = con.execute("SELECT username FROM users WHERE role='admin' AND active=1").fetchall()
    con.close()
    if admins and a.yes:
        ok(f"admin exists: {admins[0]['username']}")
    else:
        if a.yes:
            user, pw = a.user or "admin", a.password
            if not pw:
                fail("--yes needs --password for the admin account"); sys.exit(1)
        else:
            default = admins[0]["username"] if admins else "admin"
            user = input(f"  Admin username [{default}]: ").strip() or default
            while True:
                pw = getpass.getpass("  Password (min 8 chars): ")
                if len(pw) < 8:
                    print("    too short, try again"); continue
                if pw == getpass.getpass("  Repeat password: "):
                    break
                print("    passwords differ, try again")
        auth.add_user(user, pw, "admin")
        auth.set_role(user, "admin")
        ok(f"admin '{user}' ready (password stored as salted scrypt hash)")

    # ---- 5. Secure permissions ---------------------------------------------
    step(5, "Securing data files")
    try:
        import backup
        for f, m in backup.harden():
            pass
        ok("databases 0600, documents/ and backups/ 0700"
           + (" (best effort on Windows - enable BitLocker, see SECURITY.md)"
              if os.name == "nt" else ""))
    except Exception as e:
        fail(f"hardening skipped: {e}")

    # ---- 6. First backup ----------------------------------------------------
    step(6, "First backup snapshot")
    try:
        import backup
        p, n = backup.snapshot()
        ok(f"{os.path.basename(p)} ({n} files; nightly automation: see docs/MANUAL.md#install-setup-installation Part 8)")
    except Exception as e:
        fail(f"backup failed: {e}")

    # ---- 7. Self-check -------------------------------------------------------
    step(7, "Self-check")
    try:
        from app import app
        c = app.test_client()
        assert c.get("/", follow_redirects=False).status_code == 302, "login redirect"
        assert c.get("/login").status_code == 200, "login page"
        ok("application responds; login screen ready")
        for db in ("customers.db", "suppliers.db", "fuel_history.db", "security.db"):
            assert os.path.exists(db), db
        ok("all four databases present")
    except Exception as e:
        fail(f"self-check problem: {e}")
        sys.exit(1)

    # ---- done -----------------------------------------------------------------
    scheme = "https" if tls.resolve() else "http"
    starter = "start.bat" if os.name == "nt" else "./start.sh"
    print(f"""
{BOLD}=== Setup complete ==={END}

  1. Start the application:   {BOLD}{starter}{END}
  2. Open in your browser:    {BOLD}{scheme}://localhost:8050{END}
  3. Sign in with the admin account you just created.
  4. Create your colleagues in the Admin panel (role 'viewer' by default).

  Daily usage guide:  docs/MANUAL.md#user-manual-fleet-fuel-vat-refund-system
  Team/server setup:  docs/MANUAL.md#install-setup-installation (Parts 5-8: permanent service, proxy, backups)
""")


if __name__ == "__main__":
    main()
