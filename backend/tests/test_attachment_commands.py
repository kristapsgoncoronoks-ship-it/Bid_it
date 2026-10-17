"""R015: command replay differs from byte deduplication; bounded async input."""

import asyncio
import threading

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditEvent
from app.models.issued_invoice import IssuedInvoiceAttachment
from app.services import filesec
from tests.test_issuing import _activate


async def invoice(client):
    await _activate(client)
    response = await client.post(
        "/api/v1/issued",
        json={
            "buyer_name": "Synthetic attachment buyer",
            "lines": [
                {
                    "description": "Synthetic work",
                    "quantity": "1",
                    "unit_price": "10",
                    "vat_rate": "0",
                }
            ],
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def upload(
    key="synthetic-attachment-command", data=b"Synthetic supporting text", filename="synthetic.txt"
):
    return {"headers": {"Idempotency-Key": key}, "files": {"file": (filename, data, "text/plain")}}


@pytest.mark.asyncio
async def test_replayed_upload_has_one_row_and_audit(auth_client, db_session):
    iid = await invoice(auth_client)
    path = f"/api/v1/issued/{iid}/attachments"
    first = await auth_client.post(path, **upload())
    second = await auth_client.post(path, **upload())
    assert await db_session.scalar(select(func.count()).select_from(IssuedInvoiceAttachment)) == 1
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "issued.attachment")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_same_bytes_different_commands_are_distinct_intents_and_changed_payload_conflicts(
    auth_client, db_session
):
    iid = await invoice(auth_client)
    path = f"/api/v1/issued/{iid}/attachments"
    a = await auth_client.post(path, **upload("synthetic-first-command"))
    b = await auth_client.post(path, **upload("synthetic-second-command"))
    assert a.status_code == b.status_code == 201 and a.json()["id"] != b.json()["id"]
    assert (
        await auth_client.post(path, **upload("synthetic-first-command", b"Changed"))
    ).status_code == 409
    assert (
        await auth_client.post(path, **upload("synthetic-first-command", filename="renamed.txt"))
    ).status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(IssuedInvoiceAttachment)) == 2
    await auth_client.delete(path + "/" + a.json()["id"])
    result = await auth_client.get(
        f"/api/v1/issued/{iid}/attachment-commands/synthetic-first-command"
    )
    assert result.status_code == 200 and result.json()["deleted_at"] is not None
    assert (await auth_client.post(path, **upload("synthetic-first-command"))).status_code == 409


@pytest.mark.asyncio
async def test_bounded_reader_never_requests_whole_file(monkeypatch):
    monkeypatch.setattr(filesec.settings, "max_upload_mb", 1)

    class Stream:
        consumed = 0

        async def read(self, size=-1):
            assert 0 < size <= 65536
            self.consumed += size
            return b"x" * size

    stream = Stream()
    with pytest.raises(filesec.FileTooLarge):
        await filesec.read_bounded(stream)
    assert stream.consumed == filesec.max_bytes() + 1


@pytest.mark.asyncio
async def test_scanner_is_off_loop_and_fails_closed(auth_client, db_session, monkeypatch):
    iid = await invoice(auth_client)
    main_thread = threading.get_ident()
    observed = []

    def unavailable(data):
        observed.append(threading.get_ident())
        raise filesec.FileRejected("Synthetic configured scanner unavailable")

    monkeypatch.setattr(filesec, "reject_active_content", unavailable)
    response = await auth_client.post(f"/api/v1/issued/{iid}/attachments", **upload())
    assert response.status_code == 415
    assert observed and observed[0] != main_thread
    assert await db_session.scalar(select(func.count()).select_from(IssuedInvoiceAttachment)) == 0


@pytest.mark.asyncio
async def test_chunked_multipart_body_refuses_before_consuming_entire_stream(
    auth_client, monkeypatch
):
    iid = await invoice(auth_client)
    monkeypatch.setattr(filesec.settings, "max_upload_mb", 1)
    produced = 0

    async def chunks():
        nonlocal produced
        yield b'--synthetic-boundary\r\nContent-Disposition: form-data; name="file"; filename="synthetic.txt"\r\nContent-Type: text/plain\r\n\r\n'
        for _ in range(300):
            produced += 1
            yield b"x" * 8192
            await asyncio.sleep(0)
        yield b"\r\n--synthetic-boundary--\r\n"

    response = await auth_client.post(
        f"/api/v1/issued/{iid}/attachments",
        content=chunks(),
        headers={
            "Idempotency-Key": "synthetic-stream-command",
            "Content-Type": "multipart/form-data; boundary=synthetic-boundary",
        },
    )
    assert response.status_code == 413 and produced < 300


@pytest.mark.asyncio
async def test_access_revoked_during_scan_cannot_commit_attachment(
    auth_client, db_session, monkeypatch
):
    from sqlalchemy import update

    from app.models.user import User

    iid = await invoice(auth_client)

    async def revoke(data):
        await db_session.execute(update(User).values(is_active=False))
        await db_session.commit()

    monkeypatch.setattr(filesec, "reject_active_content_async", revoke)
    response = await auth_client.post(f"/api/v1/issued/{iid}/attachments", **upload())
    assert response.status_code == 401
    assert await db_session.scalar(select(func.count()).select_from(IssuedInvoiceAttachment)) == 0


@pytest.mark.asyncio
async def test_lost_response_status_lookup_and_corrupt_storage_are_recoverable(
    auth_client, db_session, monkeypatch
):
    from app.api.routes import issued as route

    iid = await invoice(auth_client)
    path = f"/api/v1/issued/{iid}/attachments"
    original = route.IssuedAttachmentOut.model_validate
    first = True

    def response_lost(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            raise RuntimeError("Synthetic response lost after commit")
        return original(*args, **kwargs)

    monkeypatch.setattr(route.IssuedAttachmentOut, "model_validate", response_lost)
    with pytest.raises(RuntimeError, match="Synthetic response"):
        await auth_client.post(path, **upload())
    result = await auth_client.get(
        f"/api/v1/issued/{iid}/attachment-commands/synthetic-attachment-command"
    )
    assert result.status_code == 200
    replay = await auth_client.post(path, **upload())
    assert replay.status_code == 200 and replay.json()["id"] == result.json()["id"]
    assert await db_session.scalar(select(func.count()).select_from(IssuedInvoiceAttachment)) == 1

    async def corrupt(*args, **kwargs):
        return b"Synthetic corrupted bytes"

    monkeypatch.setattr(route.documents, "load", corrupt)
    bad = await auth_client.get(path + f"/{result.json()['id']}/download")
    assert bad.status_code == 503 and "integrity" in bad.json()["detail"]


@pytest.mark.asyncio
async def test_valid_upload_requires_command_identity_and_exact_file_cap(auth_client, monkeypatch):
    iid = await invoice(auth_client)
    path = f"/api/v1/issued/{iid}/attachments"
    assert (
        await auth_client.post(path, files={"file": ("synthetic.txt", b"x", "text/plain")})
    ).status_code == 428
    monkeypatch.setattr(filesec.settings, "max_upload_mb", 1)
    assert (
        await auth_client.post(path, **upload(data=b"x" * filesec.max_bytes()))
    ).status_code == 201
    assert (
        await auth_client.post(
            path, **upload("synthetic-over-limit", b"x" * (filesec.max_bytes() + 1))
        )
    ).status_code == 413

    oversized = upload("synthetic-envelope-limit", b"x" * (2 * filesec.max_bytes()))
    oversized["headers"]["Origin"] = "http://localhost:5173"
    response = await auth_client.post(path, **oversized)
    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
