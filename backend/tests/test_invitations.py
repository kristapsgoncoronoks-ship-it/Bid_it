"""Invitation email + expiry (Slice 5).

Creating an invite now emails the invitee (recorded to the outbox) and stamps a
14-day expiry; the preview distinguishes expired (410) from invalid/used (404),
and accepting an expired invite is refused.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.email_message import EmailMessage
from app.models.invitation import Invitation


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


async def _owner(client, email="owner@acme.io") -> str:
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


@pytest.mark.asyncio
async def test_creating_an_invite_emails_the_invitee_and_sets_expiry(client, db_session):
    tok = await _owner(client)
    r = await client.post(
        "/api/v1/team/invites", json={"email": "new@acme.io", "role": "user"}, headers=_h(tok)
    )
    assert r.status_code == 201, r.text
    assert r.json()["expires_at"] is not None

    # An invitation email is recorded in the outbox (kind 'invitation').
    msg = await db_session.scalar(select(EmailMessage).where(EmailMessage.kind == "invitation"))
    assert msg is not None and msg.to_email == "new@acme.io"
    assert "invited" in msg.body.lower() and "accept" in msg.body.lower()


@pytest.mark.asyncio
async def test_preview_ok_then_expired_is_410(client, db_session):
    tok = await _owner(client)
    invite_token = (
        await client.post(
            "/api/v1/team/invites", json={"email": "new@acme.io", "role": "user"}, headers=_h(tok)
        )
    ).json()["token"]

    # Fresh invite previews fine.
    ok = await client.get(f"/api/v1/auth/invite/{invite_token}")
    assert ok.status_code == 200 and ok.json()["organization_name"] == "Acme"

    # Force it expired → the preview returns 410 (distinct "expired" state).
    inv = await db_session.scalar(select(Invitation).where(Invitation.token == invite_token))
    inv.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    gone = await client.get(f"/api/v1/auth/invite/{invite_token}")
    assert gone.status_code == 410


@pytest.mark.asyncio
async def test_invalid_token_is_404_not_410(client):
    r = await client.get("/api/v1/auth/invite/nope-not-real")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_accepting_an_expired_invite_is_refused(client, db_session):
    tok = await _owner(client)
    invite_token = (
        await client.post(
            "/api/v1/team/invites", json={"email": "new@acme.io", "role": "user"}, headers=_h(tok)
        )
    ).json()["token"]
    inv = await db_session.scalar(select(Invitation).where(Invitation.token == invite_token))
    inv.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    r = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invite_token, "name": "New", "password": "supersecret"},
    )
    assert r.status_code == 404  # expired invites cannot be accepted


@pytest.mark.asyncio
async def test_fresh_invite_can_still_be_accepted(client):
    tok = await _owner(client)
    invite_token = (
        await client.post(
            "/api/v1/team/invites", json={"email": "new@acme.io", "role": "user"}, headers=_h(tok)
        )
    ).json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invite_token, "name": "New", "password": "supersecret"},
    )
    assert acc.status_code == 201
    assert acc.json()["organization"]["name"] == "Acme"
    assert acc.json()["user"]["email"] == "new@acme.io"
