"""Storage read limits apply to consumed bytes, not remote metadata or read chunking."""

import io
from types import SimpleNamespace

import pytest

from app.core import storage
from app.services import documents


@pytest.mark.parametrize("backend", ["memory", "local", "s3"])
@pytest.mark.parametrize("length", [0, 7, 8, 9])
def test_backend_read_boundary_and_no_truncated_success(backend, length, tmp_path):
    data = b"x" * length
    body = io.BytesIO(data)
    if backend == "s3":
        store = storage.S3Storage.__new__(storage.S3Storage)
        store._prefix = ""
        store._bucket = "synthetic"
        store._client = SimpleNamespace(get_object=lambda **kw: {"Body": body, "ContentLength": 0})
    else:
        store = (
            storage.MemoryStorage() if backend == "memory" else storage.LocalStorage(str(tmp_path))
        )
        store.put("source", data)
    if length > 8:
        with pytest.raises(storage.StorageReadTooLarge):
            store.get("source", max_bytes=8)
    else:
        assert store.get("source", max_bytes=8) == data
    if backend == "s3":
        assert body.closed
    else:
        assert store.get("source") == data  # no deletion or mutation on refusal


@pytest.mark.parametrize("failure", [False, True])
def test_streaming_reads_are_bounded_across_short_chunks_and_always_close(failure):
    class Body(io.BytesIO):
        consumed = 0
        requested = []

        def read(self, size=-1):
            assert 0 < size <= 65536
            self.requested.append(size)
            if failure and self.consumed >= 2000:
                raise OSError("private synthetic provider diagnostic")
            result = super().read(min(size, 2000))
            self.consumed += len(result)
            return result

    body = Body(b"x" * 200000)
    store = storage.S3Storage.__new__(storage.S3Storage)
    store._prefix = ""
    store._bucket = "synthetic"
    store._client = SimpleNamespace(get_object=lambda **kw: {"Body": body, "ContentLength": 1})
    expected = storage.StorageError if failure else storage.StorageReadTooLarge
    with pytest.raises(expected):
        store.get("source", max_bytes=70000)
    assert body.closed
    assert body.consumed == (2000 if failure else 70001)


@pytest.mark.parametrize("backend", ["memory", "local", "s3"])
@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_limits_are_refused_before_backend_io(backend, limit, tmp_path):
    store = storage.MemoryStorage() if backend == "memory" else storage.LocalStorage(str(tmp_path))
    if backend == "s3":
        store = storage.S3Storage.__new__(storage.S3Storage)  # no client; any I/O would fail
    with pytest.raises(ValueError):
        store.get("source", max_bytes=limit)


def test_local_read_stops_at_limit_plus_one_and_closes(tmp_path, monkeypatch):
    from pathlib import Path

    store = storage.LocalStorage(str(tmp_path))
    store.put("source", b"x" * 200000)
    original = Path.open
    observed = []

    class File:
        def __init__(self, file):
            self.file = file
            self.consumed = 0
            observed.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def read(self, size=-1):
            assert 0 < size <= 65536
            data = self.file.read(size)
            self.consumed += len(data)
            return data

    monkeypatch.setattr(Path, "open", lambda path, *a, **kw: File(original(path, *a, **kw)))
    with pytest.raises(storage.StorageReadTooLarge):
        store.get("source", max_bytes=70000)
    assert len(observed) == 1 and observed[0].file.closed
    assert observed[0].consumed == 70001


@pytest.mark.asyncio
async def test_capture_load_without_reference_does_no_io(monkeypatch):
    monkeypatch.setattr(
        storage, "get_storage", lambda: pytest.fail("No reference should do no I/O")
    )
    assert await documents.load_capture(documents.UPLOADS, "synthetic", None) is None


def test_truncated_real_s3_stream_fails_and_closes_instead_of_returning_partial_bytes():
    from botocore.response import StreamingBody

    raw = io.BytesIO(b"partial")
    body = StreamingBody(raw, 100)
    store = storage.S3Storage.__new__(storage.S3Storage)
    store._prefix = ""
    store._bucket = "synthetic"
    store._client = SimpleNamespace(get_object=lambda **kw: {"Body": body, "ContentLength": 100})
    with pytest.raises(storage.StorageError) as error:
        store.get("source", max_bytes=200)
    assert not isinstance(error.value, storage.StorageReadTooLarge)
    assert raw.closed
