"""Attachment downloads bound object growth by their recorded admission size."""

import io
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core import storage
from app.models.issued_invoice import IssuedInvoiceAttachment
from app.services import documents, filesec
from tests.test_attachment_commands import invoice, upload


async def _attachment(client, db, data=b"Synthetic supporting document"):
    iid = await invoice(client)
    response = await client.post(f"/api/v1/issued/{iid}/attachments", **upload(data=data))
    assert response.status_code == 201, response.text
    row = await db.get(IssuedInvoiceAttachment, response.json()["id"])
    key = storage.content_key(documents.ISSUED_ATTACHMENTS, row.org_id, row.sha256)
    return row, f"/api/v1/issued/{iid}/attachments/{row.id}/download", key


class ObservedBody:
    def __init__(self, source, *, short_reads=False):
        self.source = source
        self.short_reads = short_reads
        self.requests = []
        self.consumed = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def read(self, size=-1):
        self.requests.append(size)
        requested = min(size, 3) if self.short_reads and size > 0 else size
        data = self.source.read(requested)
        self.consumed += len(data)
        return data

    def close(self):
        self.source.close()


def _refused(response):
    assert response.status_code == 503
    assert "integrity" in response.json()["detail"].lower()
    assert "restore" in response.json()["detail"].lower()
    assert "content-disposition" not in response.headers


@pytest.mark.asyncio
async def test_sparse_attachment_growth_stops_at_recorded_size_plus_one_and_recovers(
    auth_client, db_session, tmp_path, monkeypatch
):
    store = storage.LocalStorage(str(tmp_path))
    storage.set_storage(store)
    original = b"Synthetic supporting document"
    row, url, key = await _attachment(auth_client, db_session, original)
    original_identity = (row.sha256, row.size, row.command_key, row.command_hash)
    path = store._path(key)
    replaced_size = 32 * 1024 * 1024
    with path.open("r+b") as source:
        source.truncate(replaced_size)
    original_open = Path.open
    observed = []

    def observe(candidate, *args, **kwargs):
        source = original_open(candidate, *args, **kwargs)
        if candidate == path and args == ("rb",):
            source = ObservedBody(source)
            observed.append(source)
        return source

    monkeypatch.setattr(Path, "open", observe)
    refused = await auth_client.get(url)
    _refused(refused)
    assert len(observed) == 1
    assert observed[0].consumed == len(original) + 1, {
        "consumed": observed[0].consumed,
        "requests": observed[0].requests,
    }
    assert all(0 < size <= 65536 for size in observed[0].requests)
    assert observed[0].source.closed
    assert key not in refused.text and str(tmp_path) not in refused.text
    assert path.stat().st_size == replaced_size
    await db_session.refresh(row)
    assert (row.sha256, row.size, row.command_key, row.command_hash) == original_identity

    store.put(key, original)
    restored = await auth_client.get(url)
    assert restored.status_code == 200 and restored.content == original
    assert observed[-1].source.closed
    assert restored.headers["content-disposition"].startswith("attachment;")
    assert restored.headers["x-content-type-options"] == "nosniff"
    assert restored.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_remote_attachment_short_reads_ignore_false_length_and_close(auth_client, db_session):
    original = b"Synthetic supporting document"
    row, url, key = await _attachment(auth_client, db_session, original)
    body = ObservedBody(io.BytesIO(original + b"Synthetic extra bytes"), short_reads=True)
    calls = []

    def get_object(**kwargs):
        calls.append(kwargs)
        return {"Body": body, "ContentLength": 1}

    store = object.__new__(storage.S3Storage)
    store._bucket, store._prefix = "synthetic-bucket", ""
    store._client = SimpleNamespace(get_object=get_object)
    storage.set_storage(store)
    refused = await auth_client.get(url)
    _refused(refused)
    assert calls == [{"Bucket": "synthetic-bucket", "Key": key}]
    assert body.consumed == row.size + 1, body.requests
    assert all(0 < size <= 65536 for size in body.requests)
    assert body.source.closed

    store._client = SimpleNamespace(
        get_object=lambda **kwargs: {"Body": io.BytesIO(original), "ContentLength": 99999999}
    )
    recovered = await auth_client.get(url)
    assert recovered.status_code == 200 and recovered.content == original


@pytest.mark.asyncio
async def test_historical_attachment_survives_lower_upload_limit(
    auth_client, db_session, monkeypatch
):
    monkeypatch.setattr(filesec.settings, "max_upload_mb", 3)
    original = b"x" * (2 * 1024 * 1024)
    row, url, _ = await _attachment(auth_client, db_session, original)
    monkeypatch.setattr(filesec.settings, "max_upload_mb", 1)
    response = await auth_client.get(url)
    assert response.status_code == 200 and response.content == original
    assert row.size > filesec.max_bytes()
    assert storage.sha256_hex(response.content) == row.sha256


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [b"y" * 28, b"short", None])
async def test_corrupt_truncated_or_missing_attachment_never_downloads(
    auth_client, db_session, replacement
):
    row, url, key = await _attachment(auth_client, db_session, b"x" * 28)
    store = storage.get_storage()
    if replacement is None:
        store.delete(key)
    else:
        store.put(key, replacement)
    response = await auth_client.get(url)
    assert response.status_code == 503
    assert "content-disposition" not in response.headers
    assert key not in response.text and row.sha256 not in response.text
    store.put(key, b"x" * 28)
    recovered = await auth_client.get(url)
    assert recovered.status_code == 200 and recovered.content == b"x" * 28


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, -1])
async def test_legacy_empty_and_invalid_size_metadata(auth_client, db_session, monkeypatch, size):
    row, url, _ = await _attachment(auth_client, db_session)
    sha, _ = await documents.store(documents.ISSUED_ATTACHMENTS, row.org_id, b"")
    row.sha256, row.size = sha, size
    await db_session.commit()
    original_get = storage.get_storage().get
    calls = []

    def observed(key, **kwargs):
        calls.append(kwargs)
        return original_get(key, **kwargs)

    monkeypatch.setattr(storage.get_storage(), "get", observed)
    response = await auth_client.get(url)
    if size == 0:
        assert response.status_code == 200 and response.content == b""
        assert calls == [{"max_bytes": 1}]
    else:
        _refused(response)
        assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unknown", "other_invoice", "trashed", "other_tenant"])
async def test_invisible_attachment_never_reads_storage(
    auth_client, db_session, role_client, monkeypatch, change
):
    row, url, _ = await _attachment(auth_client, db_session)
    if change == "unknown":
        url = url.replace(str(row.id), str(uuid4()))
    elif change == "other_invoice":
        url = url.replace(str(row.invoice_id), await invoice(auth_client))
    elif change == "trashed":
        assert (await auth_client.delete(url.removesuffix("/download"))).status_code == 204
    else:
        auth_client = await role_client("owner")
        await invoice(auth_client)  # issuing enabled in the second tenant too
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Invisible attachment touched object storage")

    monkeypatch.setattr(documents, "load", forbidden)
    response = await auth_client.get(url)
    assert response.status_code == 404
    assert calls == []
