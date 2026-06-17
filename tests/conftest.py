"""
Shared pytest fixtures.

The web/auth tests need at least one active admin in security.db. To avoid
clobbering any real local security.db, we back it up, install a throwaway admin
for the test session, and restore the original afterwards.
"""
import os
import shutil
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

SECURITY_DB = os.path.join(WORKDIR, "security.db")
TEST_USER = "pytest_admin"
TEST_PW = "Pytest!Pw123"


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
                             "intake_override_until", "scrape_scheduler_enabled")


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
    con.commit(); con.close()
    try:
        yield
    finally:
        con = auth.connect()
        con.execute("DELETE FROM app_settings")
        con.executemany("INSERT INTO app_settings (key, value) VALUES (?,?)", snapshot)
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
