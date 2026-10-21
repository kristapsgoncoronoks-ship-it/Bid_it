"""Calendar OAuth credentials and browser bindings never leave in a workspace ZIP."""

import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.security import decode_access_token
from app.models.calendar_connection import CalendarConnection, CalendarOAuthAttempt
from app.services import workspace_export


@pytest.mark.asyncio
async def test_workspace_zip_redacts_calendar_credentials_and_oauth_bindings(
    auth_client, db_session
):
    me = (await auth_client.get("/api/v1/auth/me")).json()
    org_id, user_id = me["organization"]["id"], me["user"]["id"]
    claims = decode_access_token(auth_client.headers["Authorization"].split()[1])
    assert claims is not None
    planted = {
        "sealed_tokens": "SYNTHETIC-CALENDAR-TOKEN-DO-NOT-EXPORT",
        "state_hash": "a17c" * 16,
        "browser_hash": "b28d" * 16,
        "sealed_verifier": "SYNTHETIC-PKCE-VERIFIER-DO-NOT-EXPORT",
    }
    connection = CalendarConnection(
        id=str(uuid.uuid4()),
        org_id=org_id,
        user_id=user_id,
        provider="google",
        status="connected",
        generation=1,
        calendar_id="synthetic-calendar",
        calendar_name="Synthetic work schedule",
        sealed_tokens=planted["sealed_tokens"],
    )
    db_session.add(connection)
    await db_session.flush()
    attempt = CalendarOAuthAttempt(
        org_id=org_id,
        user_id=user_id,
        provider="google",
        connection_id=connection.id,
        connection_generation=1,
        session_id=claims["jti"],
        state_hash=planted["state_hash"],
        browser_hash=planted["browser_hash"],
        sealed_verifier=planted["sealed_verifier"],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db_session.add(attempt)
    await db_session.commit()

    path, _summary = await workspace_export.build_zip_file(db_session, org_id)
    try:
        with zipfile.ZipFile(path) as archive:
            contents = {name: archive.read(name) for name in archive.namelist()}
    finally:
        Path(path).unlink()
    all_bytes = b"\n".join(contents.values())
    for secret in planted.values():
        assert secret.encode() not in all_bytes
    exported_connection = json.loads(contents["data/calendar_connections.jsonl"])
    exported_attempt = json.loads(contents["data/calendar_oauth_attempts.jsonl"])
    manifest = json.loads(contents["manifest.json"])
    assert exported_connection["id"] == connection.id
    assert exported_connection["sealed_tokens"] is None
    assert exported_attempt["id"] == attempt.id
    for field in ("state_hash", "browser_hash", "sealed_verifier", "session_id"):
        assert exported_attempt[field] is None
        assert field in manifest["redacted"]["calendar_oauth_attempts"]
    assert "sealed_tokens" in manifest["redacted"]["calendar_connections"]
