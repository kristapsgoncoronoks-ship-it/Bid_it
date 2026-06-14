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


@pytest.fixture()
def client(admin_session):
    """A logged-in Flask test client (CSRF-exempt login keeps this simple)."""
    import app as A
    c = A.app.test_client()
    r = c.post("/login", data={"username": admin_session["user"],
                               "password": admin_session["pw"]})
    assert r.status_code == 302, f"login failed: {r.status_code}"
    return c
