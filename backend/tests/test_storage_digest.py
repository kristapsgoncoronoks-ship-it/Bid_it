"""Integrity hashing consumes fixed chunks and never returns a partial digest."""

import asyncio
import hashlib
import io
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody

from app.core import storage
from app.services import documents

PRIVATE_DIAGNOSTIC = "synthetic-private-provider-token-and-path"


def _s3(body, *, get_object=None):
    store = storage.S3Storage.__new__(storage.S3Storage)
    store._prefix = "synthetic-prefix"
    store._bucket = "synthetic-bucket"
    store._client = SimpleNamespace(
        get_object=get_object
        or (lambda **kwargs: {"Body": body, "ContentLength": 0, "ETag": "not-sha256"})
    )
    return store


class _ShortBody(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.requests = []
        self.consumed = 0

    def read(self, size=-1):
        assert 0 < size <= 65536, "digest must use finite, bounded reads"
        self.requests.append(size)
        chunk = super().read(min(size, 2003))
        self.consumed += len(chunk)
        return chunk


@pytest.mark.parametrize("backend", ["memory", "local", "s3"])
@pytest.mark.parametrize("length", [0, 1, 65536, 65537, 200001])
def test_digest_matches_real_sha256_without_materializing_get(
    backend, length, tmp_path, monkeypatch
):
    data = (b"synthetic-original-\x00\xff" * (length // 21 + 1))[:length]
    body = _ShortBody(data)
    if backend == "s3":
        store = _s3(body)
    else:
        store = (
            storage.MemoryStorage() if backend == "memory" else storage.LocalStorage(str(tmp_path))
        )
        store.put("source", data)
    monkeypatch.setattr(store, "get", lambda *args, **kwargs: pytest.fail("Unbounded get used"))

    assert store.digest("source") == hashlib.sha256(data).hexdigest()
    if backend == "s3":
        assert body.closed and body.consumed == len(data)
        assert body.requests


def test_local_digest_bounds_every_read_and_closes(tmp_path, monkeypatch):
    data = b"synthetic-local-original" * 10000
    body = _ShortBody(data)
    store = storage.LocalStorage(str(tmp_path))
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: body)

    assert store.digest("source") == hashlib.sha256(data).hexdigest()
    assert body.closed and body.consumed == len(data)
    assert len(body.requests) > 2


@pytest.mark.parametrize("backend", ["memory", "local"])
def test_missing_digest_is_missing_not_a_read_error(backend, tmp_path):
    store = storage.MemoryStorage() if backend == "memory" else storage.LocalStorage(str(tmp_path))
    with pytest.raises(storage.StorageError) as error:
        store.digest("absent")
    assert not isinstance(error.value, storage.StorageReadError)


@pytest.mark.parametrize("code", ["NoSuchKey", "404", "NotFound"])
def test_s3_missing_codes_remain_distinct_from_failed_reads(code):
    def unavailable(**kwargs):
        raise ClientError({"Error": {"Code": code, "Message": PRIVATE_DIAGNOSTIC}}, "GetObject")

    with pytest.raises(storage.StorageError) as error:
        _s3(None, get_object=unavailable).digest("source")
    assert not isinstance(error.value, storage.StorageReadError)
    assert PRIVATE_DIAGNOSTIC not in str(error.value)


@pytest.mark.parametrize("code", ["AccessDenied", "SlowDown", "NoSuchBucket", "InternalError"])
def test_s3_provider_failures_are_sanitized_read_errors(code):
    def unavailable(**kwargs):
        raise ClientError({"Error": {"Code": code, "Message": PRIVATE_DIAGNOSTIC}}, "GetObject")

    with pytest.raises(storage.StorageReadError) as error:
        _s3(None, get_object=unavailable).digest("source")
    assert PRIVATE_DIAGNOSTIC not in str(error.value)


def test_s3_non_client_provider_failure_is_sanitized():
    def unavailable(**kwargs):
        raise OSError(PRIVATE_DIAGNOSTIC)

    with pytest.raises(storage.StorageReadError) as error:
        _s3(None, get_object=unavailable).digest("source")
    assert PRIVATE_DIAGNOSTIC not in str(error.value)


@pytest.mark.parametrize("backend", ["local", "s3"])
@pytest.mark.parametrize("failure", ["read", "update", "close"])
def test_digest_failure_closes_source_and_never_returns_partial_hash(
    backend, failure, tmp_path, monkeypatch
):
    class Body(_ShortBody):
        def read(self, size=-1):
            if failure == "read" and self.consumed > 0:
                raise OSError(PRIVATE_DIAGNOSTIC)
            return super().read(size)

        def close(self):
            super().close()
            if failure == "close":
                raise OSError(PRIVATE_DIAGNOSTIC)

    body = Body(b"source" * 10000)
    if backend == "s3":
        store = _s3(body)
    else:
        store = storage.LocalStorage(str(tmp_path))
        monkeypatch.setattr(Path, "open", lambda *args, **kwargs: body)
    if failure == "update":

        class BrokenHash:
            def update(self, data):
                raise OSError(PRIVATE_DIAGNOSTIC)

        monkeypatch.setattr(storage.hashlib, "sha256", lambda *args, **kwargs: BrokenHash())

    with pytest.raises(storage.StorageReadError) as error:
        store.digest("source")
    assert body.closed
    assert PRIVATE_DIAGNOSTIC not in str(error.value)


def test_local_open_failure_is_sanitized_and_not_reported_missing(tmp_path, monkeypatch):
    store = storage.LocalStorage(str(tmp_path))

    def unavailable(*args, **kwargs):
        raise PermissionError(PRIVATE_DIAGNOSTIC)

    monkeypatch.setattr(Path, "open", unavailable)
    with pytest.raises(storage.StorageReadError) as error:
        store.digest("source")
    assert PRIVATE_DIAGNOSTIC not in str(error.value)


def test_real_truncated_s3_body_raises_and_closes_instead_of_hashing_partial_bytes():
    raw = io.BytesIO(b"partial")
    body = StreamingBody(raw, 100)
    with pytest.raises(storage.StorageReadError):
        _s3(body).digest("source")
    assert raw.closed


def test_digest_keeps_s3_bucket_and_prefix_scope():
    observed = []

    def get_object(**kwargs):
        observed.append(kwargs)
        return {"Body": io.BytesIO(b"original")}

    assert (
        _s3(None, get_object=get_object).digest("uploads/tenant/source")
        == hashlib.sha256(b"original").hexdigest()
    )
    assert observed == [
        {"Bucket": "synthetic-bucket", "Key": "synthetic-prefix/uploads/tenant/source"}
    ]


def test_local_digest_rejects_escape_and_sibling_directory(tmp_path):
    root = tmp_path / "store"
    sibling = tmp_path / "store-evil"
    sibling.mkdir()
    (sibling / "secret").write_bytes(b"outside-root")
    store = storage.LocalStorage(str(root))
    with pytest.raises(storage.StorageError):
        store.digest("../store-evil/secret")


@pytest.mark.parametrize("backend", ["local", "s3"])
def test_base_exception_from_read_is_not_swallowed_and_source_closes(
    backend, tmp_path, monkeypatch
):
    class StopDigest(BaseException):
        pass

    class Body(io.BytesIO):
        def read(self, size=-1):
            raise StopDigest()

    body = Body(b"source")
    if backend == "s3":
        store = _s3(body)
    else:
        store = storage.LocalStorage(str(tmp_path))
        monkeypatch.setattr(Path, "open", lambda *args, **kwargs: body)
    with pytest.raises(StopDigest):
        store.digest("source")
    assert body.closed


@pytest.mark.asyncio
async def test_documents_digest_without_reference_performs_no_io(monkeypatch):
    monkeypatch.setattr(storage, "get_storage", lambda: pytest.fail("No reference must do no I/O"))
    assert await documents.digest(documents.UPLOADS, "synthetic-org", None) is None


@pytest.mark.asyncio
async def test_documents_digest_is_off_thread_and_keeps_tenant_key(monkeypatch):
    event_loop_thread = threading.get_ident()
    observed = []
    expected = hashlib.sha256(b"original").hexdigest()

    def digest(key):
        observed.append((threading.get_ident(), key))
        return expected

    monkeypatch.setattr(storage.get_storage(), "digest", digest)
    assert await documents.digest(documents.UPLOADS, "synthetic-org", expected) == expected
    assert len(observed) == 1
    assert observed[0][0] != event_loop_thread
    assert observed[0][1] == storage.content_key(documents.UPLOADS, "synthetic-org", expected)


@pytest.mark.asyncio
async def test_adapter_cancellation_propagates_and_worker_closes_after_release(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class Body(io.BytesIO):
        def read(self, size=-1):
            entered.set()
            if not release.wait(timeout=5):
                raise OSError("Synthetic blocked source was not released")
            return super().read(size)

        def close(self):
            super().close()
            closed.set()

    body = Body(b"original")
    monkeypatch.setattr(storage, "get_storage", lambda: _s3(body))
    task = asyncio.create_task(documents.digest(documents.UPLOADS, "synthetic-org", "a" * 64))
    try:
        assert await asyncio.to_thread(entered.wait, 2), "digest did not enter its worker"
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(closed.wait, 2), "source was left open after worker exit"
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
