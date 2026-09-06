"""P2 correctness rows of the 2026-09-05 audit that are small enough to share a
file: BE-016 (audit.record and database failures) and SEC-007 (login length).
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.services import audit


@pytest.mark.asyncio
async def test_be016_a_database_failure_while_auditing_is_raised_not_swallowed(
    auth_client, db_session, monkeypatch
):
    """BE-016: `audit.record` swallowed EVERY exception, including a flush that
    had already put the session in a failed state. The operation then either
    500'd at its own commit with an unrelated message, or — worse — committed
    without its audit event. Database failures now propagate; attribution and
    hashing failures stay best-effort."""

    async def _boom(*a, **k):
        raise OperationalError("SELECT 1", {}, Exception("connection lost"))

    monkeypatch.setattr(db_session, "scalar", _boom)
    with pytest.raises(OperationalError):
        await audit.record(db_session, "test.event", org_id="00000000-0000-0000-0000-000000000001")


@pytest.mark.asyncio
async def test_be016_a_hashing_failure_is_still_best_effort(db_session, monkeypatch):
    def _bad_hash(*a, **k):
        raise TypeError("unhashable meta")

    monkeypatch.setattr(audit, "_hash", _bad_hash)
    assert (
        await audit.record(db_session, "test.event", org_id="00000000-0000-0000-0000-000000000001")
        is None
    )


@pytest.mark.asyncio
async def test_sec007_the_login_password_field_is_bounded(client):
    """SEC-007: `LoginRequest.password` was unbounded — a client could push
    megabytes through a bcrypt verify. bcrypt reads 72 bytes; the bound is 200
    so a pre-cap account with a longer (truncated) password still signs in."""
    r = await client.post(
        "/api/v1/auth/login", json={"email": "owner@acme.io", "password": "x" * 5000}
    )
    assert r.status_code == 422, r.text
    r = await client.post(
        "/api/v1/auth/login", json={"email": "owner@acme.io", "password": "x" * 200}
    )
    assert r.status_code == 401, r.text  # bounded and refused as a wrong password, not a 422
