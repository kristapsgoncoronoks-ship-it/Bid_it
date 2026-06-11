"""
Auth hardening tests: scrypt verify, unknown-user constant-time path, and
transparent rehash/upgrade when a user's stored KDF cost is below target.
"""
import importlib
import os

import pytest


@pytest.fixture()
def temp_auth(tmp_path, monkeypatch):
    import auth
    importlib.reload(auth)
    db = tmp_path / "security_test.db"
    monkeypatch.setattr(auth, "DB", str(db))
    return auth


def test_verify_roundtrip(temp_auth):
    temp_auth.add_user("alice", "s3cret!", role="processor")
    assert temp_auth.verify("alice", "s3cret!") is True
    assert temp_auth.verify("alice", "wrong") is False


def test_role_permissions(temp_auth):
    temp_auth.add_user("boss", "pw12345", role="admin")
    temp_auth.add_user("alice", "pw12345", role="processor")
    # admin holds every capability incl. the admin-only boundary
    assert temp_auth.has_perm("admin", "system_setup")
    assert temp_auth.has_perm("admin", "user_admin")
    # processor defaults to all grantable capabilities, never the admin-only ones
    assert temp_auth.has_perm("processor", "data_import")
    assert not temp_auth.has_perm("processor", "system_setup")
    assert not temp_auth.has_perm("processor", "user_admin")
    # admin can revoke a capability from the processor role
    temp_auth.set_permission("processor", "pricing", False)
    assert not temp_auth.has_perm("processor", "pricing")
    # and grant it back
    temp_auth.set_permission("processor", "pricing", True)
    assert temp_auth.has_perm("processor", "pricing")


def test_admin_only_perms_not_grantable(temp_auth):
    import pytest as _pt
    with _pt.raises(AssertionError):
        temp_auth.set_permission("processor", "system_setup", True)
    with _pt.raises(AssertionError):
        temp_auth.set_permission("admin", "pricing", False)  # admin perms are fixed


def test_unknown_user_is_false(temp_auth):
    # Must not raise and must return False (constant-time path runs a dummy KDF).
    assert temp_auth.verify("nobody", "whatever") is False


def test_new_users_use_strong_n(temp_auth):
    temp_auth.add_user("bob", "pw12345")
    con = temp_auth.connect()
    row = con.execute("SELECT kdf_n FROM users WHERE username=?", ("bob",)).fetchone()
    con.close()
    assert row["kdf_n"] >= 2 ** 16


def test_rehash_upgrades_legacy_cost(temp_auth):
    # Simulate a legacy hash stored at the old n=2**14 and confirm a successful
    # login transparently upgrades it to the new target.
    import secrets
    salt = secrets.token_bytes(16)
    legacy_n = 2 ** 14
    con = temp_auth.connect()
    con.execute(
        "INSERT OR REPLACE INTO users (username, salt, pw_hash, active, role, kdf_n) "
        "VALUES (?,?,?,1,'editor',?)",
        ("carol", salt, temp_auth._hash("legacypw", salt, n=legacy_n), legacy_n),
    )
    con.commit(); con.close()

    assert temp_auth.verify("carol", "legacypw") is True
    con = temp_auth.connect()
    row = con.execute("SELECT kdf_n FROM users WHERE username=?", ("carol",)).fetchone()
    con.close()
    assert row["kdf_n"] >= 2 ** 16, "successful login should upgrade KDF cost"
    # still verifies after the upgrade
    assert temp_auth.verify("carol", "legacypw") is True
