"""CAP-CAPACITY: slow bodies must release admission without cancelling handlers."""

import asyncio
import math

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.attachment_body_limit import AttachmentBodyLimitMiddleware
from app.core.config import settings
from tests.test_upload_ingress import HEADERS, application, multipart


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/invoices/upload", "/invoices/upload/batch", "/email/inbound/mailgun", "/email/inbound"],
)
@pytest.mark.parametrize("trickle", [False, True], ids=["idle", "absolute"])
async def test_slow_body_refuses_closes_spools_and_recovers(monkeypatch, path, trickle):
    from starlette import formparsers

    monkeypatch.setitem(settings.__dict__, "upload_body_idle_seconds", 0.08 if trickle else 0.03)
    monkeypatch.setitem(settings.__dict__, "upload_body_timeout_seconds", 0.13 if trickle else 1)
    monkeypatch.setattr(settings, "upload_max_active_requests", 1)
    monkeypatch.setattr(formparsers.MultiPartParser, "spool_max_size", 32)
    original = formparsers.SpooledTemporaryFile
    spools = []

    def spool(*args, **kwargs):
        file = original(*args, **kwargs)
        spools.append(file)
        return file

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", spool)
    app, handled = application(path)
    json_body = path == "/email/inbound"
    body = b'{"subject":"' + b"x" * 500 if json_body else multipart([("file", b"x" * 500)])

    async def stalled():
        yield body[:350]
        if trickle:
            while True:
                await asyncio.sleep(0.02)
                yield b"x"
        await asyncio.Event().wait()

    headers = {"Content-Type": "application/json"} if json_body else HEADERS
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # The outer test bound detects the old indefinite hold; it is not the
        # production deadline, and remains longer than both configured budgets.
        response = await asyncio.wait_for(
            client.post("/api/v1" + path, content=stalled(), headers=headers), timeout=0.8
        )
        assert response.status_code == 408
        assert response.headers["cache-control"] == "no-store"
        assert "Check upload status before retrying" in response.json()["detail"]
        assert not handled
        assert all(f.closed for f in spools)
        if not json_body:
            assert len(spools) == 1 and spools[0]._rolled
        valid = b"{}" if json_body else multipart([("file", b"ok")])
        recovered = await client.post("/api/v1" + path, content=valid, headers=headers)
        assert recovered.status_code == 200


@pytest.mark.asyncio
async def test_initial_body_wait_is_bounded(monkeypatch):
    monkeypatch.setitem(settings.__dict__, "upload_body_idle_seconds", 0.03)
    monkeypatch.setitem(settings.__dict__, "upload_body_timeout_seconds", 1)
    app, handled = application("/invoices/upload")

    async def no_body():
        await asyncio.Event().wait()
        yield b""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await asyncio.wait_for(
            client.post("/api/v1/invoices/upload", content=no_body(), headers=HEADERS), timeout=0.8
        )
    assert response.status_code == 408 and not handled


@pytest.mark.asyncio
async def test_finished_body_does_not_cancel_following_handler_work(monkeypatch):
    monkeypatch.setitem(settings.__dict__, "upload_body_idle_seconds", 0.02)
    monkeypatch.setitem(settings.__dict__, "upload_body_timeout_seconds", 0.02)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"complete", "more_body": False}

    async def handler(scope, receive, send):
        assert (await receive())["body"] == b"complete"
        await asyncio.sleep(0.06)  # storage / command commit is NOT a body read
        await send({"type": "http.response.start", "status": 202, "headers": []})

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/v1/invoices/upload", "headers": []}
    await AttachmentBodyLimitMiddleware(handler)(scope, receive, send)
    assert sent[0]["status"] == 202


@pytest.mark.asyncio
async def test_disconnect_poll_after_complete_body_has_no_body_deadline(monkeypatch):
    monkeypatch.setitem(settings.__dict__, "upload_body_idle_seconds", 0.02)
    monkeypatch.setitem(settings.__dict__, "upload_body_timeout_seconds", 0.02)
    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": b"done", "more_body": False}
        await asyncio.sleep(0.06)
        return {"type": "http.disconnect"}

    async def handler(scope, receive, send):
        await receive()
        assert (await receive())["type"] == "http.disconnect"

    scope = {"type": "http", "method": "POST", "path": "/api/v1/invoices/upload", "headers": []}
    await AttachmentBodyLimitMiddleware(handler)(scope, receive, None)


@pytest.mark.parametrize("field", ["upload_body_idle_seconds", "upload_body_timeout_seconds"])
@pytest.mark.parametrize("value", [0, -1, math.inf, math.nan])
def test_deadline_configuration_is_positive_and_finite(field, value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        type(settings)(_env_file=None, environment="development", **{field: value})
