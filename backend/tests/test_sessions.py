"""Server-side sessions + token revocation (Slice 4).

Proves a JWT can be revoked before it expires: logout, sign-out-everywhere,
revoke-one, and a password reset all invalidate the affected token on the very
next request. A token without a live session (revoked / legacy / forged jti) is
a hard 401.
"""

import pytest

from app.core.security import create_access_token


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client, email="owner@acme.io") -> str:
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Acme",
            "name": "Owner",
            "email": email,
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]["access_token"]


async def _login(client, email="owner@acme.io") -> str:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "supersecret"})
    assert r.status_code == 200, r.text
    return r.json()["token"]["access_token"]


@pytest.mark.asyncio
async def test_logout_revokes_the_token_immediately(client):
    tok = await _register(client)
    # The token works…
    assert (await client.get("/api/v1/auth/me", headers=_h(tok))).status_code == 200
    # …logout revokes it…
    out = await client.post("/api/v1/auth/logout", headers=_h(tok))
    assert out.status_code == 200 and out.json()["logged_out"] is True
    # …and the SAME token is now rejected (not just client-side).
    assert (await client.get("/api/v1/auth/me", headers=_h(tok))).status_code == 401


@pytest.mark.asyncio
async def test_sessions_list_shows_each_login(client):
    await _register(client)
    await _login(client)
    t2 = await _login(client)
    listing = (await client.get("/api/v1/auth/sessions", headers=_h(t2))).json()
    # register + 2 logins = 3 active sessions; exactly one is flagged current.
    assert len(listing) == 3
    assert sum(1 for s in listing if s["current"]) == 1
    current = next(s for s in listing if s["current"])
    assert current["created_at"] and "id" in current


@pytest.mark.asyncio
async def test_revoke_others_keeps_current_kills_the_rest(client):
    reg = await _register(client)
    other = await _login(client)
    keep = await _login(client)

    res = await client.post("/api/v1/auth/sessions/revoke-others", headers=_h(keep))
    assert res.status_code == 200 and res.json()["revoked"] == 2

    # The kept session still works; the other two are dead.
    assert (await client.get("/api/v1/auth/me", headers=_h(keep))).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=_h(reg))).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=_h(other))).status_code == 401


@pytest.mark.asyncio
async def test_revoke_one_session_by_id(client):
    keep = await _register(client)
    victim = await _login(client)
    listing = (await client.get("/api/v1/auth/sessions", headers=_h(keep))).json()
    victim_id = next(s["id"] for s in listing if not s["current"])

    r = await client.delete(f"/api/v1/auth/sessions/{victim_id}", headers=_h(keep))
    assert r.status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=_h(victim))).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=_h(keep))).status_code == 200


@pytest.mark.asyncio
async def test_password_reset_revokes_all_sessions(client, db_session):
    from datetime import timedelta

    from sqlalchemy import select

    from app.models.email_token import PURPOSE_PASSWORD_RESET
    from app.models.user import User
    from app.services import verification

    tok = await _register(client)
    assert (await client.get("/api/v1/auth/me", headers=_h(tok))).status_code == 200

    user = await db_session.scalar(select(User).where(User.email == "owner@acme.io"))
    raw = await verification.issue(db_session, user, PURPOSE_PASSWORD_RESET, timedelta(hours=1))
    await db_session.commit()
    done = await client.post(
        "/api/v1/auth/reset-password", json={"token": raw, "new_password": "brandnewpw9"}
    )
    assert done.status_code == 200
    # Every pre-reset token is now dead.
    assert (await client.get("/api/v1/auth/me", headers=_h(tok))).status_code == 401


@pytest.mark.asyncio
async def test_token_without_a_session_is_rejected(client):
    """A validly-signed token whose jti has no session (forged / legacy) is 401 —
    the session store, not just the signature, is the gate."""
    await _register(client)
    # Forge a token for a random user id + nonexistent jti (signature is valid).
    forged = create_access_token(
        "00000000-0000-0000-0000-000000000000",
        {"org": "x", "jti": "11111111-1111-1111-1111-111111111111"},
    )
    assert (await client.get("/api/v1/auth/me", headers=_h(forged))).status_code == 401


@pytest.mark.asyncio
async def test_old_jwt_key_fallback_preserves_revocation_gate(client, monkeypatch):
    """SEC-JWT-001: a staged rotation changes signature VERIFICATION only. A token
    signed under the old key survives while that key is a verify-only fallback,
    but the server-side jti/session row stays authoritative — logout still kills
    it on the next request."""
    from app.core.config import settings

    old_key = "old-signing-key-0123456789abcdef"
    new_key = "new-signing-key-0123456789abcdef"
    monkeypatch.setattr(settings, "jwt_signing_key", old_key)
    monkeypatch.setattr(settings, "jwt_signing_key_fallbacks", [])

    token = await _register(client, email="rotation@acme.io")

    monkeypatch.setattr(settings, "jwt_signing_key", new_key)
    monkeypatch.setattr(settings, "jwt_signing_key_fallbacks", [old_key])

    assert (await client.get("/api/v1/auth/me", headers=_h(token))).status_code == 200
    assert (await client.post("/api/v1/auth/logout", headers=_h(token))).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=_h(token))).status_code == 401


@pytest.mark.asyncio
async def test_retired_fallback_invalidates_old_tokens_and_new_tokens_use_the_active_key(
    client, monkeypatch
):
    from jose import jwt

    from app.core.config import settings

    old_key = "old-signing-key-0123456789abcdef"
    new_key = "new-signing-key-0123456789abcdef"
    monkeypatch.setattr(settings, "jwt_signing_key", old_key)
    monkeypatch.setattr(settings, "jwt_signing_key_fallbacks", [])
    old_token = await _register(client, email="retire@acme.io")

    monkeypatch.setattr(settings, "jwt_signing_key", new_key)
    monkeypatch.setattr(settings, "jwt_signing_key_fallbacks", [])  # retired at once
    assert (await client.get("/api/v1/auth/me", headers=_h(old_token))).status_code == 401

    new_token = await _register(client, email="fresh@acme.io")
    # Signed with the ACTIVE key only — never with secret_key while a signing key is set.
    jwt.decode(new_token, new_key, algorithms=[settings.jwt_algorithm])
    with pytest.raises(Exception):
        jwt.decode(new_token, settings.secret_key, algorithms=[settings.jwt_algorithm])


@pytest.mark.asyncio
async def test_token_subject_must_own_the_session(client):
    """SEC-012 (found by the R3 review's probe): with the signing key in hand an
    attacker forged {sub: victim, jti: <attacker's OWN live session>} and was
    served as the victim — the session row existed and was live, and nobody
    checked whose it was. A forged token now needs a live session OF THE
    VICTIM, which revocation does kill."""
    from jose import jwt as _jwt

    from app.core.security import create_access_token

    victim = await _register(client, email="victim@acme.io")
    attacker = await _register(client, email="attacker@evil.io")
    victim_sub = _jwt.get_unverified_claims(victim)["sub"]
    attacker_jti = _jwt.get_unverified_claims(attacker)["jti"]
    forged = create_access_token(victim_sub, {"jti": attacker_jti})
    assert (await client.get("/api/v1/auth/me", headers=_h(forged))).status_code == 401
    # The attacker's own token still works; the victim's too.
    assert (await client.get("/api/v1/auth/me", headers=_h(attacker))).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=_h(victim))).status_code == 200


@pytest.mark.asyncio
async def test_first_enablement_of_jwt_signing_key_keeps_secret_key_tokens_only_as_a_fallback(
    client, monkeypatch
):
    """Q-1: tokens minted before JWT_SIGNING_KEY existed were signed with
    SECRET_KEY. Enabling the key without listing SECRET_KEY as a fallback logs
    everyone out; listing it keeps them for the staged window."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "jwt_signing_key", None)
    monkeypatch.setattr(settings, "jwt_signing_key_fallbacks", [])
    token = await _register(client, email="before@acme.io")

    monkeypatch.setattr(settings, "jwt_signing_key", "new-signing-key-0123456789abcdef")
    monkeypatch.setattr(settings, "jwt_signing_key_fallbacks", [settings.secret_key])
    assert (await client.get("/api/v1/auth/me", headers=_h(token))).status_code == 200
    monkeypatch.setattr(settings, "jwt_signing_key_fallbacks", [])
    assert (await client.get("/api/v1/auth/me", headers=_h(token))).status_code == 401
