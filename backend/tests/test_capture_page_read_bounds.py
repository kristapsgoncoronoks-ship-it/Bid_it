"""Generated page downloads never allocate an unbounded restored object."""

import io
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.core import storage
from app.models.extraction_run import ExtractionRun
from app.schemas.capture_pages import CapturePage, CapturePages
from app.services import capture_pages, documents


async def _page(auth_client, db_session, *, length=None):
    org_id = (await auth_client.get("/api/v1/auth/me")).json()["organization"]["id"]
    output = io.BytesIO()
    with Image.new("RGB", (8, 8), "white") as image:
        image.save(output, "JPEG")
    data = output.getvalue()
    if length is not None:
        data += b"\0" * (length - len(data))
    sha, size = await documents.store(
        capture_pages.PREFIX, org_id, data, "image/jpeg", db=db_session
    )
    source_sha, _ = await documents.store(
        documents.UPLOADS, org_id, b"Synthetic original", db=db_session
    )
    run = ExtractionRun(
        org_id=org_id,
        method="text-layer",
        source_sha256=source_sha,
        source_filename="original.pdf",
        preview_json=CapturePages(
            status="ready",
            total_pages=1,
            pages=[CapturePage(number=1, sha256=sha, size=size, width=8, height=8)],
        ).model_dump_json(),
    )
    db_session.add(run)
    await db_session.commit()
    return run, data, storage.content_key(capture_pages.PREFIX, org_id, sha)


def _path(run):
    return f"/api/v1/invoices/captures/{run.id}/pages/1/doc"


@pytest.mark.asyncio
async def test_sparse_restored_page_stops_at_cap_plus_one_and_recovers(
    auth_client, db_session, tmp_path, monkeypatch
):
    store = storage.LocalStorage(str(tmp_path))
    storage.set_storage(store)
    run, original, key = await _page(auth_client, db_session)
    original_manifest = run.preview_json
    source_key = storage.content_key(documents.UPLOADS, run.org_id, run.source_sha256)
    stored_path = store._path(key)
    restored_size = capture_pages.MAX_PAGE_BYTES * 64
    with stored_path.open("r+b") as restored:
        restored.truncate(restored_size)
    original_open = Path.open
    observations = []

    class ObservedFile:
        def __init__(self, source):
            self.source = source
            self.consumed = 0
            self.requests = []
            observations.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def close(self):
            self.source.close()

        def read(self, size=-1):
            self.requests.append(size)
            result = self.source.read(size)
            self.consumed += len(result)
            return result

    def observe(path, *args, **kwargs):
        source = original_open(path, *args, **kwargs)
        if path == stored_path and args == ("rb",):
            return ObservedFile(source)
        return source

    monkeypatch.setattr(Path, "open", observe)
    refused = await auth_client.get(_path(run))
    assert refused.status_code == 503, {
        "response": refused.text,
        "consumed": [item.consumed for item in observations],
        "requests": [item.requests for item in observations],
        "closed": [item.source.closed for item in observations],
    }
    assert "integrity" in refused.json()["detail"].lower()
    assert "restore" in refused.json()["detail"].lower()
    assert "original document" in refused.json()["detail"].lower()
    assert "content-disposition" not in refused.headers
    assert str(tmp_path) not in refused.text and key not in refused.text
    assert len(observations) == 1
    assert observations[0].consumed == capture_pages.MAX_PAGE_BYTES + 1
    assert all(0 < size <= 65536 for size in observations[0].requests)
    assert observations[0].source.closed
    assert stored_path.stat().st_size == restored_size
    assert store.get(source_key) == b"Synthetic original"
    await db_session.refresh(run)
    assert run.preview_json == original_manifest

    store.put(key, original)
    recovered = await auth_client.get(_path(run))
    assert recovered.status_code == 200 and recovered.content == original
    assert observations[-1].source.closed
    assert recovered.headers["content-type"] == "image/jpeg"
    assert recovered.headers["content-disposition"].startswith("inline;")
    assert recovered.headers["x-content-type-options"] == "nosniff"
    assert recovered.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_remote_page_ignores_false_content_length_and_closes_oversized_body(
    auth_client, db_session
):
    run, original, key = await _page(auth_client, db_session)

    class Body(io.BytesIO):
        consumed = 0
        requests = []

        def read(self, size=-1):
            self.requests.append(size)
            data = super().read(size)
            self.consumed += len(data)
            return data

    body = Body(original + b"\0" * capture_pages.MAX_PAGE_BYTES)
    store = storage.S3Storage.__new__(storage.S3Storage)
    store._prefix = ""
    store._bucket = "synthetic"
    requested = []

    def object_for(**kwargs):
        requested.append(kwargs)
        return {"Body": body, "ContentLength": 1}

    store._client = SimpleNamespace(get_object=object_for)
    storage.set_storage(store)
    response = await auth_client.get(_path(run))
    assert response.status_code == 503, {
        "response": response.text,
        "consumed": body.consumed,
        "requests": body.requests,
        "closed": body.closed,
    }
    assert "restore" in response.json()["detail"].lower()
    assert body.closed and body.consumed == capture_pages.MAX_PAGE_BYTES + 1
    assert all(0 < size <= 65536 for size in body.requests)
    assert requested == [{"Bucket": "synthetic", "Key": key}]


@pytest.mark.asyncio
async def test_page_at_existing_generated_limit_downloads_exactly(auth_client, db_session):
    run, data, _ = await _page(auth_client, db_session, length=capture_pages.MAX_PAGE_BYTES)
    response = await auth_client.get(_path(run))
    assert response.status_code == 200 and response.content == data
    assert len(response.content) == capture_pages.MAX_PAGE_BYTES


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["size", "hash", "missing"])
async def test_small_corrupt_or_missing_page_keeps_existing_opaque_response(
    auth_client, db_session, fault
):
    run, data, key = await _page(auth_client, db_session)
    store = storage.get_storage()
    if fault == "missing":
        store.delete(key)
    else:
        store.put(key, data + b"x" if fault == "size" else b"x" + data[1:])
    response = await auth_client.get(_path(run))
    assert response.status_code == 404
    assert response.json()["detail"] == "Stored page preview missing"
    assert "content-disposition" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["malformed", "unknown", "missing-page", "other-tenant"])
async def test_opaque_page_refusals_do_not_read_storage(
    auth_client, db_session, monkeypatch, target
):
    run, _, _ = await _page(auth_client, db_session)
    url = _path(run)
    headers = {}
    if target == "malformed":
        url = url.replace(run.id, "not-a-uuid")
    elif target == "unknown":
        url = url.replace(run.id, str(uuid4()))
    elif target == "missing-page":
        url = url.replace("/pages/1/", "/pages/2/")
    else:
        registered = await auth_client.post(
            "/api/v1/auth/register",
            json={
                "organization_name": "Synthetic other workspace",
                "name": "Other owner",
                "email": "other-page-bounds@example.com",
                "password": "synthetic-other-page-bounds-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers["Authorization"] = "Bearer " + registered.json()["token"]["access_token"]
    monkeypatch.setattr(storage, "get_storage", lambda: pytest.fail("Denied page must do no I/O"))
    response = await auth_client.get(url, headers=headers)
    assert response.status_code == 404
    assert "content-disposition" not in response.headers
