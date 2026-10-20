"""S3 source outages remain retryable without disclosing object/provider details."""

from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from httpx import ASGITransport, AsyncClient

from app.core import storage
from app.main import app
from tests.test_local_read_error_http import _download
from tests.test_local_storage_read_errors import ObservedFile

PRIVATE = "synthetic-private-s3-source-diagnostic"


class _RemoteFile(ObservedFile):
    def read(self, size=None):
        # StreamingBody uses None for its unbounded raw-stream read.
        return super().read(-1 if size is None else size)


def _remote(local, key, fault):
    path = local._path(key)
    original = path.read_bytes()
    calls, handles = [], []
    state = {"fault": fault}

    def get_object(**kwargs):
        calls.append(kwargs)
        assert kwargs == {"Bucket": "synthetic-bucket", "Key": f"synthetic-prefix/{key}"}
        mode = state["fault"]
        if mode in {"denied", "missing"}:
            raise ClientError(
                {
                    "Error": {
                        "Code": "AccessDenied" if mode == "denied" else "NoSuchKey",
                        "Message": PRIVATE,
                    }
                },
                "GetObject",
            )
        source = _RemoteFile(
            path.open("rb"),
            read_error=OSError(PRIVATE) if mode == "read" else None,
            close_error=OSError(PRIVATE) if mode == "close" else None,
            successful_reads=1,
        )
        handles.append(source)
        length = len(original) + (1 if mode == "truncated" else 0)
        return {"Body": StreamingBody(source, length), "ContentLength": length}

    remote = storage.S3Storage.__new__(storage.S3Storage)
    remote._bucket = "synthetic-bucket"
    remote._prefix = "synthetic-prefix"
    remote._client = SimpleNamespace(get_object=get_object)
    storage.set_storage(remote)
    return path, original, state, calls, handles


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("surface", "fault"),
    [("source", "denied"), ("source", "truncated"), ("page", "read"), ("page", "close")],
)
async def test_s3_outage_is_sanitized_503_and_same_download_recovers(
    auth_client, db_session, tmp_path, caplog, surface, fault
):
    local, url, key = await _download(auth_client, db_session, tmp_path, surface)
    path, original, state, calls, handles = _remote(local, key, fault)
    caplog.clear()
    refused = await auth_client.get(url)
    assert refused.status_code == 503
    assert refused.json()["code"] == "storage_read_unavailable"
    assert "retry" in refused.json()["detail"].lower()
    assert refused.headers["cache-control"] == "private, no-store"
    assert "content-disposition" not in refused.headers
    assert PRIVATE not in refused.text and PRIVATE not in caplog.text
    assert key not in refused.text and str(path) not in refused.text
    assert len(calls) == 1
    assert all(h.source.closed and h.close_calls == 1 for h in handles)
    assert path.read_bytes() == original
    state["fault"] = None
    recovered = await auth_client.get(url)
    assert recovered.status_code == 200 and recovered.content == original
    assert recovered.headers["x-content-type-options"] == "nosniff"
    assert recovered.headers["cache-control"] == "private, no-store"
    assert all(h.source.closed and h.close_calls == 1 for h in handles)


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["source", "page"])
async def test_s3_true_missing_retains_opaque_404(auth_client, db_session, tmp_path, surface):
    local, url, key = await _download(auth_client, db_session, tmp_path, surface)
    _, _, _, calls, handles = _remote(local, key, "missing")
    response = await auth_client.get(url)
    assert response.status_code == 404
    assert "content-disposition" not in response.headers
    assert PRIVATE not in response.text and key not in response.text
    assert len(calls) == 1 and not handles


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["source", "page"])
async def test_unauthenticated_download_never_reaches_s3(
    auth_client, db_session, tmp_path, surface
):
    local, url, key = await _download(auth_client, db_session, tmp_path, surface)
    _, _, _, calls, handles = _remote(local, key, "denied")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(url)
    assert response.status_code == 401
    assert not calls and not handles
