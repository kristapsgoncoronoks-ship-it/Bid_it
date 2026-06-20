"""
Shared pytest fixtures.

The web/auth tests need at least one active admin in security.db. To avoid
clobbering any real local security.db, we back it up, install a throwaway admin
for the test session, and restore the original afterwards.

Per-test DATA-DB isolation (the 5 demo DBs: customers/suppliers/fuel_history/
vat_claims/benchmark) is achieved by REDIRECTING the single data root: a session
baseline holds pristine copies; an autouse function fixture copies them into a
per-test temp dir and points ``FFS_DATA_DIR`` there (resolved AT CALL TIME by
``paths.db_path`` everywhere). No file is swapped under an open connection, so the
WAL/busy_timeout deadlock that file-restore-per-test would cause is avoided. A
session safety net hashes the REAL repo DBs and asserts they are never written.
"""
import hashlib
import os
import shutil
import sys
import tempfile

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

SECURITY_DB = os.path.join(WORKDIR, "security.db")
TEST_USER = "pytest_admin"
TEST_PW = "Pytest!Pw123"

# The five redirectable data DBs (paths.db_path resolves them under FFS_DATA_DIR).
# security.db is platform-managed and deliberately NOT in this set.
DEMO_DBS = ("customers.db", "suppliers.db", "fuel_history.db",
            "vat_claims.db", "benchmark.db")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="session")
def _demo_db_baseline():
    """A pristine, read-only SOURCE copy of the 5 demo DBs, made ONCE at session start
    from the repo dir. Per-test copies are taken from here (not the live repo files),
    so even if the safety net below missed a leak the baseline stays clean."""
    base = tempfile.mkdtemp(prefix="ffs_demo_baseline_")
    for name in DEMO_DBS:
        src = os.path.join(WORKDIR, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(base, name))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_demo_dbs(_demo_db_baseline, tmp_path, monkeypatch):
    """Redirect the data root to a per-test temp dir seeded with FRESH copies of the 5
    demo DBs, so every test reads/writes its OWN copies. ``paths.db_path`` (and every
    file-opening site that calls it at call time) honors ``FFS_DATA_DIR`` immediately;
    monkeypatch + tmp_path auto-clean. Default behavior (env unset) is untouched."""
    data = tmp_path / "data"
    data.mkdir()
    for name in DEMO_DBS:
        src = os.path.join(_demo_db_baseline, name)
        if os.path.exists(src):
            shutil.copy2(src, str(data / name))
    monkeypatch.setenv("FFS_DATA_DIR", str(data))


@pytest.fixture(scope="session", autouse=True)
def _assert_real_dbs_untouched():
    """SAFETY NET: hash the REAL repo demo DBs at session start and assert each is
    byte-identical at session end. A failure names the file whose resolution point
    still writes the live DB (i.e. a path that wasn't redirected through paths.db_path)."""
    before = {name: _sha256(os.path.join(WORKDIR, name))
              for name in DEMO_DBS if os.path.exists(os.path.join(WORKDIR, name))}
    yield
    failures = []
    for name, digest in before.items():
        p = os.path.join(WORKDIR, name)
        if not os.path.exists(p):
            failures.append(f"{name} (vanished)")
        elif _sha256(p) != digest:
            failures.append(name)
    assert not failures, (
        "real repo demo DBs were modified during the test session — an unredirected "
        f"resolution point wrote them: {failures}")


@pytest.fixture(scope="session")
def admin_session():
    """Back up security.db, install a throwaway admin, restore on teardown."""
    backup = SECURITY_DB + ".pytest.bak"
    had_db = os.path.exists(SECURITY_DB)
    if had_db:
        shutil.copy2(SECURITY_DB, backup)
    import auth
    auth.add_user(TEST_USER, TEST_PW, role="admin")
    try:
        yield {"user": TEST_USER, "pw": TEST_PW}
    finally:
        # Restore robustly: if the backup is present, move it back; if it has
        # vanished (interrupted/partial run), DON'T raise FileNotFoundError out of
        # teardown — that turns into a spurious, order-dependent suite casualty.
        if os.path.exists(backup):
            shutil.move(backup, SECURITY_DB)
        elif not had_db:
            # there was no security.db before; remove the one we created
            try:
                os.remove(SECURITY_DB)
            except OSError:
                pass
        # else: had a db but the backup is gone — leave the live security.db as-is
        #       rather than failing teardown.


# Settings keys (or key prefixes) that are GLOBAL, persisted in security.db's
# app_settings table, and toggled by tests (module on/off switches, AI feature
# flags, the multitenant switch, intake override, the scrape scheduler). A test
# that flips one of these without restoring it pollutes every later test AND
# every later run (security.db persists). We neutralise this two ways below.
_RUNTIME_SETTING_PREFIXES = ("module_", "ai_", "multitenant",
                             "intake_override_until", "scrape_scheduler_enabled",
                             "sso_", "intake_autopilot_enabled",
                             "dokobit_", "invoice_fee_vat_pct", "invoice_issuer_",
                             "trust_cloudflare", "cloudflare_only",
                             "cloudflare_ip_ranges", "cloudflare_trusted_proxies",
                             "twofa_email_enabled")


def _is_runtime_setting(key):
    return any(key == p or key.startswith(p) for p in _RUNTIME_SETTING_PREFIXES)


@pytest.fixture(autouse=True)
def _isolate_app_settings(admin_session):
    """Per-test isolation of the global ``app_settings`` table (security.db).

    BEFORE each test:
      * snapshot the full settings table, then DELETE the runtime/toggleable keys
        (``module_*``, ``ai_*``, ``multitenant``, ``intake_override_until``,
        ``scrape_scheduler_enabled``) so the test starts from the in-code defaults
        regardless of any leaked OR hostile pre-existing state (e.g. a stray
        ``module_sharing=0`` set before the run). Modules default to "on", AI
        flags to OFF — the clean product baseline.
    AFTER each test:
      * restore the snapshot verbatim, so no test leaks state to the next one and
        we leave the developer's security.db exactly as we found it.
    """
    import auth
    con = auth.connect()
    snapshot = [(r["key"], r["value"])
                for r in con.execute("SELECT key, value FROM app_settings")]
    to_clear = [k for (k, _v) in snapshot if _is_runtime_setting(k)]
    con.executemany("DELETE FROM app_settings WHERE key=?", [(k,) for k in to_clear])
    # role_permissions (processor capabilities) is ALSO global mutable state in
    # security.db — a test that revokes/grants a capability would otherwise leak
    # into another (and across runs). Snapshot it, then reset to the in-code clean
    # default (every grantable capability GRANTED for 'processor'), so each test
    # starts identical regardless of order or a polluted dev DB.
    perm_snap = [(r["role"], r["perm"], r["allowed"])
                 for r in con.execute("SELECT role, perm, allowed FROM role_permissions")]
    con.execute("DELETE FROM role_permissions")
    con.executemany("INSERT INTO role_permissions (role, perm, allowed) VALUES ('processor',?,1)",
                    [(p,) for p in auth.PERMISSIONS])
    con.commit(); con.close()
    try:
        yield
    finally:
        con = auth.connect()
        con.execute("DELETE FROM app_settings")
        con.executemany("INSERT INTO app_settings (key, value) VALUES (?,?)", snapshot)
        con.execute("DELETE FROM role_permissions")
        con.executemany("INSERT INTO role_permissions (role, perm, allowed) VALUES (?,?,?)", perm_snap)
        con.commit(); con.close()


@pytest.fixture()
def client(admin_session):
    """A logged-in Flask test client (CSRF-exempt login keeps this simple)."""
    import app as A
    c = A.app.test_client()
    r = c.post("/login", data={"username": admin_session["user"],
                               "password": admin_session["pw"]})
    assert r.status_code == 302, f"login failed: {r.status_code}"
    return c
