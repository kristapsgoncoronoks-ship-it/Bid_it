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
        json={"organization_name": "Acme", "name": "Owner", "email": email, "password": "supersecret"},
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]["access_token"]


async def _login(client, email="owner@acme.io") -> str:
    r = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret"}
    )
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
