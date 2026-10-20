"""Local read failures never escape recovery or return partly trusted bytes."""

from pathlib import Path

import pytest

from app.core import storage

PRIVATE_DIAGNOSTIC = "synthetic-private-local-path-and-token"
READ_ERROR = "Stored document could not be read. Check storage and retry."
ORIGINAL = b"Synthetic original supporting document"
KEY = "synthetic-source"


class ObservedFile:
    """Inject faults around a real descriptor, including cleanup failures."""

    def __init__(self, source, *, read_error=None, close_error=None, successful_reads=0):
        self.source = source
        self.read_error = read_error
        self.close_error = close_error
        self.successful_reads = successful_reads
        self.requests = []
        self.consumed = 0
        self.close_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def read(self, size=-1):
        self.requests.append(size)
        if self.read_error is not None and len(self.requests) > self.successful_reads:
            raise self.read_error
        # Bounded reads exercise failure after partial progress; an unbounded
        # read still exercises the existing one-call get contract.
        data = self.source.read(min(size, 3) if size > 0 else size)
        self.consumed += len(data)
        return data

    def close(self):
        self.close_calls += 1
        self.source.close()
        if self.close_error is not None:
            raise self.close_error


def _source(tmp_path):
    store = storage.LocalStorage(str(tmp_path / "store"))
    store.put(KEY, ORIGINAL)
    return store, store._path(KEY)


def _observe(monkeypatch, target, **faults):
    original_open = Path.open
    observed = []

    def open_source(path, *args, **kwargs):
        source = original_open(path, *args, **kwargs)
        if path == target and (args[0] if args else kwargs.get("mode")) == "rb":
            source = ObservedFile(source, **faults)
            observed.append(source)
        return source

    monkeypatch.setattr(Path, "open", open_source)
    return observed


def _sanitized(error, tmp_path):
    assert str(error) == READ_ERROR
    assert PRIVATE_DIAGNOSTIC not in str(error)
    assert KEY not in str(error)
    assert str(tmp_path) not in str(error)


@pytest.mark.parametrize("limit", [None, 128])
@pytest.mark.parametrize("error_type", [OSError, PermissionError, FileNotFoundError])
@pytest.mark.parametrize("stage", ["read", "close"])
def test_local_read_and_close_failures_are_sanitized_and_recoverable(
    tmp_path, monkeypatch, limit, error_type, stage
):
    store, path = _source(tmp_path)
    failure = error_type(PRIVATE_DIAGNOSTIC)
    faults = {f"{stage}_error": failure}
    if stage == "read" and limit is not None:
        faults["successful_reads"] = 1
    with monkeypatch.context() as patch:
        observed = _observe(patch, path, **faults)
        with pytest.raises(storage.StorageReadError) as error:
            store.get(KEY, max_bytes=limit)
        _sanitized(error.value, tmp_path)
        assert error.value.__cause__ is failure
        assert len(observed) == 1
        assert observed[0].source.closed and observed[0].close_calls == 1
        if stage == "close":
            assert observed[0].consumed == len(ORIGINAL)
        elif limit is not None:
            assert observed[0].consumed == 3
            assert all(0 < size <= 65536 for size in observed[0].requests)
    assert path.read_bytes() == ORIGINAL
    assert store.get(KEY, max_bytes=limit) == ORIGINAL


@pytest.mark.parametrize("limit", [None, 128])
@pytest.mark.parametrize("error_type", [OSError, PermissionError])
def test_local_open_failure_is_not_missing_and_recovers(tmp_path, monkeypatch, limit, error_type):
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
            store.get(KEY, max_bytes=limit)
        _sanitized(error.value, tmp_path)
        assert error.value.__cause__ is failure
    assert store.get(KEY, max_bytes=limit) == ORIGINAL


@pytest.mark.parametrize("limit", [None, 128])
def test_real_directory_cannot_be_reported_as_a_missing_object(tmp_path, limit):
    store = storage.LocalStorage(str(tmp_path / "store"))
    path = store._path(KEY)
    path.mkdir()
    with pytest.raises(storage.StorageReadError) as error:
        store.get(KEY, max_bytes=limit)
    _sanitized(error.value, tmp_path)
    assert isinstance(error.value.__cause__, IsADirectoryError)
    assert path.is_dir()
    path.rmdir()
    store.put(KEY, ORIGINAL)
    assert store.get(KEY, max_bytes=limit) == ORIGINAL


@pytest.mark.parametrize("limit", [None, 128])
def test_only_a_missing_open_is_classified_as_missing(tmp_path, limit):
    store = storage.LocalStorage(str(tmp_path / "store"))
    with pytest.raises(storage.StorageError) as error:
        store.get(KEY, max_bytes=limit)
    assert not isinstance(error.value, storage.StorageReadError)
    assert str(error.value) == "Stored document is missing."
    assert isinstance(error.value.__cause__, FileNotFoundError)
    assert KEY not in str(error.value) and str(tmp_path) not in str(error.value)
    store.put(KEY, ORIGINAL)
    assert store.get(KEY, max_bytes=limit) == ORIGINAL


def test_oversize_refusal_survives_a_secondary_close_error(tmp_path, monkeypatch):
    store, path = _source(tmp_path)
    with monkeypatch.context() as patch:
        observed = _observe(patch, path, close_error=OSError(PRIVATE_DIAGNOSTIC))
        with pytest.raises(storage.StorageReadTooLarge) as error:
            store.get(KEY, max_bytes=8)
        assert PRIVATE_DIAGNOSTIC not in str(error.value)
        assert len(observed) == 1
        assert observed[0].source.closed and observed[0].close_calls == 1
        assert observed[0].consumed == 9
    assert store.get(KEY) == ORIGINAL


@pytest.mark.parametrize("limit", [None, 128])
def test_primary_read_failure_survives_a_secondary_close_error(tmp_path, monkeypatch, limit):
    store, path = _source(tmp_path)
    read_failure = FileNotFoundError("synthetic read interruption")
    close_failure = OSError(PRIVATE_DIAGNOSTIC)
    with monkeypatch.context() as patch:
        observed = _observe(patch, path, read_error=read_failure, close_error=close_failure)
        with pytest.raises(storage.StorageReadError) as error:
            store.get(KEY, max_bytes=limit)
        _sanitized(error.value, tmp_path)
        assert error.value.__cause__ is read_failure
        assert len(observed) == 1
        assert observed[0].source.closed and observed[0].close_calls == 1
    assert store.get(KEY) == ORIGINAL


@pytest.mark.parametrize("limit", [None, 128])
@pytest.mark.parametrize("close_fails", [False, True])
@pytest.mark.parametrize("failure_type", [BaseException, RuntimeError])
def test_non_io_read_failure_is_preserved_and_source_closes(
    tmp_path, monkeypatch, limit, close_fails, failure_type
):
    store, path = _source(tmp_path)
    failure = failure_type("synthetic worker interruption")
    close_failure = OSError(PRIVATE_DIAGNOSTIC) if close_fails else None
    with monkeypatch.context() as patch:
        observed = _observe(patch, path, read_error=failure, close_error=close_failure)
        with pytest.raises(failure_type) as error:
            store.get(KEY, max_bytes=limit)
        assert error.value is failure
        assert len(observed) == 1
        assert observed[0].source.closed and observed[0].close_calls == 1
    assert store.get(KEY) == ORIGINAL


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_limit_is_rejected_before_local_path_or_open(tmp_path, monkeypatch, limit):
    store, _ = _source(tmp_path)
    monkeypatch.setattr(
        store, "_path", lambda *args: pytest.fail("invalid limit must not inspect a path")
    )
    with pytest.raises(ValueError, match="must be positive"):
        store.get(KEY, max_bytes=limit)


@pytest.mark.parametrize("key", ["../outside", "../store-evil/source"])
@pytest.mark.parametrize("limit", [None, 128])
def test_path_escape_remains_a_refusal_before_open(tmp_path, monkeypatch, key, limit):
    store = storage.LocalStorage(str(tmp_path / "store"))
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("escape opened a file"))
    with pytest.raises(storage.StorageError, match="illegal key path") as error:
        store.get(key, max_bytes=limit)
    assert not isinstance(error.value, storage.StorageReadError)


@pytest.mark.parametrize("limit", [None, len(ORIGINAL)])
def test_successful_real_file_is_read_and_closed_once(tmp_path, monkeypatch, limit):
    store, path = _source(tmp_path)
    observed = _observe(monkeypatch, path)
    assert store.get(KEY, max_bytes=limit) == ORIGINAL
    assert len(observed) == 1
    assert observed[0].source.closed and observed[0].close_calls == 1
    assert observed[0].consumed == len(ORIGINAL)
    if limit is not None:
        assert all(0 < size <= 65536 for size in observed[0].requests)
