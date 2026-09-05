"""SEC-001 — an IdP-provisioned account has NO password, and nothing anyone
can type signs it in.

The defect: `oidc` and `scim` stored `hash_password("!sso-no-password")` /
`hash_password("!scim-no-password")` — real bcrypt hashes of two literals that
sit in the public source tree — and `/auth/login` verified the submitted
password against them like any other. The literal WAS the password of every
SSO- or SCIM-provisioned user, administrators included. These tests hold the
fix in every direction it has to hold:

- the literal is refused at the login route for a user provisioned by each
  path (this is the reproduction; it fails on the old code);
- the sentinel is not a hash and verifies against nothing, and `verify_password`
  refuses a non-bcrypt value before touching the library;
- the data migration retires an EXISTING legacy hash and leaves a real
  password alone;
- structurally, no code path hashes the retired literals again.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import select

from app.core import security
from app.models.organization import Organization
from app.models.user import User

APP = pathlib.Path(__file__).resolve().parent.parent / "app"
LEGACY = security.LEGACY_UNUSABLE_LITERALS


# --- the reproduction, at the route ----------------------------------------


@pytest.mark.asyncio
async def test_sec001_the_old_literal_does_not_sign_in_a_scim_provisioned_user(
    auth_client, db_session
):
    from app.services import scim

    org_id = await db_session.scalar(select(Organization.id))
    user = await scim.create_user(
        db_session, org_id, {"userName": "provisioned@corp.example"}, default_role="user"
    )
    assert not security.has_usable_password(user.hashed_password)

    for literal in LEGACY + ("", "!", "password"):
        r = await auth_client.post(
            "/api/v1/auth/login",
            json={"email": "provisioned@corp.example", "password": literal},
        )
        assert r.status_code == 401, (literal, r.text)


@pytest.mark.asyncio
async def test_sec001_the_old_literal_does_not_sign_in_a_jit_sso_user(auth_client, db_session):
    from app.models.sso import SsoConnection
    from app.services import oidc

    org_id = await db_session.scalar(select(Organization.id))
    conn = SsoConnection(
        org_id=org_id,
        slug="acme",
        protocol="oidc",
        enabled=True,
        issuer="https://idp.example.com",
        client_id="client",
        jit_enabled=True,
        default_role="user",
        groups_claim="groups",
    )
    db_session.add(conn)
    await db_session.commit()
    user, _ = await oidc._match_or_provision(
        db_session, conn, email="jit@corp.example", name="JIT", mapped_role=None
    )
    await db_session.commit()
    assert user.hashed_password == security.UNUSABLE_PASSWORD_HASH

    for literal in LEGACY:
        r = await auth_client.post(
            "/api/v1/auth/login", json={"email": "jit@corp.example", "password": literal}
        )
        assert r.status_code == 401, (literal, r.text)


# --- the primitive -----------------------------------------------------------


def test_sec001_the_sentinel_is_not_a_hash_and_verifies_nothing():
    sentinel = security.unusable_password_hash()
    assert sentinel == "!"
    assert not security.has_usable_password(sentinel)
    assert not security.has_usable_password(None)
    assert not security.has_usable_password("")
    for candidate in LEGACY + ("!", "", "x" * 72):
        assert security.verify_password(candidate, sentinel) is False
    # A real hash still works both ways — the fix must not lock everyone out.
    real = security.hash_password("wheel-chock-8")
    assert security.has_usable_password(real)
    assert security.verify_password("wheel-chock-8", real) is True
    assert security.verify_password("wheel-chock-9", real) is False


def test_sec001_a_legacy_hash_is_recognised_and_a_real_password_is_not():
    """The migration's predicate: the two old hashes are the backdoor; a user
    who set a real password through the reset flow is not touched."""
    for literal in LEGACY:
        assert security.is_legacy_unusable_hash(security.hash_password(literal))
    assert not security.is_legacy_unusable_hash(security.hash_password("a-real-password-1"))
    assert not security.is_legacy_unusable_hash(security.UNUSABLE_PASSWORD_HASH)
    assert not security.is_legacy_unusable_hash(None)


# --- the data migration ------------------------------------------------------


@pytest.mark.asyncio
async def test_sec001_migration_retires_existing_legacy_hashes_only(auth_client, db_session):
    """Runs the migration body against the harness DB: a row carrying the old
    hash becomes the sentinel; a row with a real password keeps it. Exercised
    through the same predicate + UPDATE the Alembic revision uses."""
    import importlib.util

    org_id = await db_session.scalar(select(Organization.id))
    legacy_user = User(
        org_id=org_id,
        email="legacy-sso@corp.example",
        name="Legacy",
        hashed_password=security.hash_password(LEGACY[0]),
    )
    real_hash = security.hash_password("kept-password-77")
    real_user = User(
        org_id=org_id, email="real@corp.example", name="Real", hashed_password=real_hash
    )
    db_session.add_all([legacy_user, real_user])
    await db_session.commit()

    # Before: the legacy literal IS a working password. The route reproduction.
    r = await auth_client.post(
        "/api/v1/auth/login", json={"email": "legacy-sso@corp.example", "password": LEGACY[0]}
    )
    assert r.status_code == 200, "premise: the old hash verifies — this is the backdoor"

    spec = importlib.util.spec_from_file_location(
        "sec001_mig",
        pathlib.Path(__file__).resolve().parent.parent
        / "alembic/versions/c3e5a7b9d1f2_sec001_retire_unusable_password_hashes.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    # Drive the revision's body over the harness connection.
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    conn = await db_session.connection()

    def _run(sync_conn):
        ctx = MigrationContext.configure(sync_conn)
        with Operations.context(ctx):
            mod.upgrade()

    await conn.run_sync(_run)
    await db_session.commit()
    db_session.expire_all()

    legacy_after = await db_session.scalar(
        select(User.hashed_password).where(User.email == "legacy-sso@corp.example")
    )
    real_after = await db_session.scalar(
        select(User.hashed_password).where(User.email == "real@corp.example")
    )
    assert legacy_after == security.UNUSABLE_PASSWORD_HASH
    assert real_after == real_hash

    # After: the literal is refused; the real password still works.
    r = await auth_client.post(
        "/api/v1/auth/login", json={"email": "legacy-sso@corp.example", "password": LEGACY[0]}
    )
    assert r.status_code == 401
    r = await auth_client.post(
        "/api/v1/auth/login", json={"email": "real@corp.example", "password": "kept-password-77"}
    )
    assert r.status_code == 200, r.text


# --- structural ---------------------------------------------------------------


def test_sec001_nothing_hashes_the_retired_literals_again():
    """The literals may appear ONLY in `security.LEGACY_UNUSABLE_LITERALS` (for
    the migration's predicate). A provisioning path that hashes a constant is
    the defect returning."""
    offenders = []
    for path in APP.rglob("*.py"):
        text = path.read_text()
        if path.name == "security.py" and path.parent.name == "core":
            continue
        for lit in LEGACY:
            if lit in text:
                offenders.append(f"{path.relative_to(APP)}: {lit}")
        if "_UNUSABLE_PASSWORD" in text:
            offenders.append(f"{path.relative_to(APP)}: _UNUSABLE_PASSWORD constant")
    assert offenders == [], offenders
