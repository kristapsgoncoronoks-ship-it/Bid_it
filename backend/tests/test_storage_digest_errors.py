"""A failed local hash must not claim missing bytes or hide the primary failure."""

import asyncio
import hashlib
from pathlib import Path

import pytest

from app.core import storage
from app.core.config import settings

PRIVATE_DIAGNOSTIC = "synthetic-private-digest-path-and-token"
READ_ERROR = "Stored document could not be read. Check storage and retry."
KEY = "synthetic-digest-source"
ORIGINAL = bytes(range(256)) * 512 + b"original tail"


class _StopDigest(BaseException):
    pass


class _ObservedFile:
    """Faults surround a real file descriptor; short reads still reach actual EOF."""

    def __init__(self, source, *, read_error=None, close_error=None):
        self.source = source
        self.read_error = read_error
        self.close_error = close_error
        self.requests = []
        self.consumed = 0
        self.close_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def read(self, size=-1):
        assert 0 < size <= 65536, "digest must request finite chunks at most 64 KiB"
        self.requests.append(size)
        if self.read_error is not None and len(self.requests) > 1:
            raise self.read_error
        chunk = self.source.read(min(size, 4093))
        self.consumed += len(chunk)
        return chunk

    def close(self):
        self.close_calls += 1
        self.source.close()
        if self.close_error is not None:
            raise self.close_error


def _source(tmp_path, data=ORIGINAL):
    store = storage.LocalStorage(str(tmp_path / "objects"))
    store.put(KEY, data)
    return store, store._path(KEY)


def _observe(monkeypatch, target, **faults):
    original_open = Path.open
    handles = []

    def open_source(path, *args, **kwargs):
        source = original_open(path, *args, **kwargs)
        if path == target and args == ("rb",):
            source = _ObservedFile(source, **faults)
            handles.append(source)
        return source

    monkeypatch.setattr(Path, "open", open_source)
    return handles


def _closed_once(handles):
    assert len(handles) == 1
    handle = handles[0]
    assert handle.source.closed and handle.close_calls == 1
    assert handle.requests and all(0 < size <= 65536 for size in handle.requests)
    return handle


def _sanitized(error, tmp_path):
    assert str(error) == READ_ERROR
    assert PRIVATE_DIAGNOSTIC not in str(error)
    assert KEY not in str(error) and str(tmp_path) not in str(error)


def _break_hash(monkeypatch, failure):
    class BrokenHash:
        def update(self, data):
            raise failure

    monkeypatch.setattr(storage.hashlib, "sha256", lambda *args, **kwargs: BrokenHash())


@pytest.mark.parametrize("error_type", [OSError, PermissionError, FileNotFoundError])
@pytest.mark.parametrize("stage", ["read", "update", "close"])
def test_post_open_digest_failures_are_sanitized_read_errors_and_recover(
    tmp_path, monkeypatch, error_type, stage
):
    store, path = _source(tmp_path)
    failure = error_type(PRIVATE_DIAGNOSTIC)
    expected = hashlib.sha256(ORIGINAL).hexdigest()
    with monkeypatch.context() as patch:
        faults = {stage + "_error": failure} if stage in {"read", "close"} else {}
        handles = _observe(patch, path, **faults)
        if stage == "update":
            _break_hash(patch, failure)
        with pytest.raises(storage.StorageReadError) as error:
            store.digest(KEY)
        _sanitized(error.value, tmp_path)
        assert error.value.__cause__ is failure
        handle = _closed_once(handles)
        assert handle.consumed == (len(ORIGINAL) if stage == "close" else 4093)
    assert path.read_bytes() == ORIGINAL
    assert store.digest(KEY) == expected


@pytest.mark.parametrize("error_type", [OSError, PermissionError])
def test_digest_open_error_is_sanitized_and_recovers(tmp_path, monkeypatch, error_type):
    store, path = _source(tmp_path)
    original_open = Path.open
    failure = error_type(PRIVATE_DIAGNOSTIC)

    def unavailable(candidate, *args, **kwargs):
        if candidate == path and args == ("rb",):
            raise failure
        return original_open(candidate, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", unavailable)
        with pytest.raises(storage.StorageReadError) as error:
            store.digest(KEY)
        _sanitized(error.value, tmp_path)
        assert error.value.__cause__ is failure
    assert store.digest(KEY) == hashlib.sha256(ORIGINAL).hexdigest()


def test_only_missing_at_open_is_a_missing_digest(tmp_path):
    store = storage.LocalStorage(str(tmp_path / "objects"))
    with pytest.raises(storage.StorageError) as error:
        store.digest(KEY)
    assert not isinstance(error.value, storage.StorageReadError)
    assert str(error.value) == "Stored document is missing."
    assert isinstance(error.value.__cause__, FileNotFoundError)
    assert KEY not in str(error.value) and str(tmp_path) not in str(error.value)
    store.put(KEY, ORIGINAL)
    assert store.digest(KEY) == hashlib.sha256(ORIGINAL).hexdigest()


def test_directory_at_digest_key_is_unavailable_and_can_be_restored(tmp_path):
    store = storage.LocalStorage(str(tmp_path / "objects"))
    path = store._path(KEY)
    path.mkdir()
    with pytest.raises(storage.StorageReadError) as error:
        store.digest(KEY)
    _sanitized(error.value, tmp_path)
    assert isinstance(error.value.__cause__, IsADirectoryError)
    path.rmdir()
    store.put(KEY, ORIGINAL)
    assert store.digest(KEY) == hashlib.sha256(ORIGINAL).hexdigest()


@pytest.mark.parametrize("read_error", [OSError, FileNotFoundError])
@pytest.mark.parametrize("close_error", [OSError, FileNotFoundError])
def test_secondary_close_error_cannot_replace_primary_digest_io_failure(
    tmp_path, monkeypatch, read_error, close_error
):
    store, path = _source(tmp_path)
    primary = read_error("synthetic primary interruption")
    secondary = close_error(PRIVATE_DIAGNOSTIC)
    with monkeypatch.context() as patch:
        handles = _observe(patch, path, read_error=primary, close_error=secondary)
        with pytest.raises(storage.StorageReadError) as error:
            store.digest(KEY)
        _sanitized(error.value, tmp_path)
        assert error.value.__cause__ is primary
        assert _closed_once(handles).consumed == 4093
    assert path.read_bytes() == ORIGINAL
    assert store.digest(KEY) == hashlib.sha256(ORIGINAL).hexdigest()


@pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError, _StopDigest])
@pytest.mark.parametrize("close_fails", [False, True])
@pytest.mark.parametrize("stage", ["read", "update"])
def test_digest_non_io_failure_and_cancellation_survive_cleanup(
    tmp_path, monkeypatch, failure_type, close_fails, stage
):
    store, path = _source(tmp_path)
    primary = failure_type("synthetic worker interruption")
    secondary = OSError(PRIVATE_DIAGNOSTIC) if close_fails else None
    with monkeypatch.context() as patch:
        handles = _observe(
            patch, path, read_error=primary if stage == "read" else None, close_error=secondary
        )
        if stage == "update":
            _break_hash(patch, primary)
        with pytest.raises(failure_type) as error:
            store.digest(KEY)
        assert error.value is primary
        assert _closed_once(handles).consumed == 4093
    assert store.digest(KEY) == hashlib.sha256(ORIGINAL).hexdigest()


@pytest.mark.parametrize("length", [0, 1, 65536, 65537, 2 * 1024 * 1024 + 17])
def test_digest_keeps_bounded_chunks_and_historical_files_above_current_upload_limit(
    tmp_path, monkeypatch, length
):
    original = (bytes(range(256)) * (length // 256 + 1))[:length]
    expected = hashlib.sha256(original).hexdigest()
    store, path = _source(tmp_path, original)
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    monkeypatch.setattr(store, "get", lambda *args, **kwargs: pytest.fail("digest called get"))
    handles = _observe(monkeypatch, path)
    assert store.digest(KEY) == expected
    assert _closed_once(handles).consumed == length
    if length > 1024 * 1024:
        assert len(handles[0].requests) > 2


@pytest.mark.asyncio
async def test_integrity_http_reports_post_open_missing_as_retryable_error_then_recovers(
    auth_client, db_session, tmp_path, monkeypatch
):
    from sqlalchemy import select

    from app.models.issuer import IssuerProfile
    from app.services import documents
    from tests.test_integrity import _upload_logo

    await _upload_logo(auth_client)
    profile = await db_session.scalar(select(IssuerProfile))
    original = await documents.load(documents.LOGOS, profile.org_id, profile.logo_sha256)
    store = storage.LocalStorage(str(tmp_path / "objects"))
    key = storage.content_key(documents.LOGOS, profile.org_id, profile.logo_sha256)
    store.put(key, original)
    path = store._path(key)
    monkeypatch.setattr(storage, "_storage", store)
    with monkeypatch.context() as patch:
        handles = _observe(patch, path, read_error=FileNotFoundError(PRIVATE_DIAGNOSTIC))
        response = await auth_client.post("/api/v1/integrity/documents/verify")
        assert response.status_code == 200
        body = response.json()
        assert body["checked"] == 1 and body["ok"] == 0 and not body["healthy"]
        assert len(body["issues"]) == 1 and body["issues"][0]["problem"] == "error"
        assert body["issues"][0]["detail"] == READ_ERROR
        assert PRIVATE_DIAGNOSTIC not in response.text and str(tmp_path) not in response.text
        _closed_once(handles)
    assert path.read_bytes() == original
    restored = await auth_client.post("/api/v1/integrity/documents/verify")
    assert restored.status_code == 200
    assert restored.json() == {"checked": 1, "ok": 1, "healthy": True, "issues": []}
