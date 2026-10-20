"""S3 lookup failures and interrupted streams must retain truthful classifications."""

import asyncio
import hashlib

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, IncompleteReadError
from botocore.response import StreamingBody

from app.core import storage

PRIVATE_DIAGNOSTIC = "synthetic-private-provider-path-and-token"
BUCKET = "synthetic-private-bucket"
PREFIX = "synthetic-private-prefix"
KEY = "uploads/synthetic-private-tenant/original"
READ_ERROR = "Stored document could not be read. Check storage and retry."
MISSING_ERROR = "Stored document is missing."
ORIGINAL = bytes(range(256)) * 512 + b"synthetic original tail"


class _StopRead(BaseException):
    pass


class _Client:
    def __init__(self, response=None, failure=None):
        self.response = response
        self.failure = failure
        self.calls = []

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return self.response


class _Body:
    """Short reads and injected faults wrap a real opened file descriptor."""

    def __init__(self, source, *, read_error=None, close_error=None):
        self.source = source
        self.read_error = read_error
        self.close_error = close_error
        self.requests = []
        self.consumed = 0
        self.close_calls = 0

    def read(self, size=-1):
        self.requests.append(size)
        if self.read_error is not None and len(self.requests) > 1:
            raise self.read_error
        data = self.source.read(min(size, 4093) if size > 0 else size)
        self.consumed += len(data)
        return data

    def close(self):
        self.close_calls += 1
        self.source.close()
        if self.close_error is not None:
            raise self.close_error


class _Stream(StreamingBody):
    def __init__(self, source, length):
        super().__init__(source, length)
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        super().close()


def _source(tmp_path):
    path = tmp_path / "synthetic-s3-object"
    path.write_bytes(ORIGINAL)
    return path


def _store(client):
    store = storage.S3Storage.__new__(storage.S3Storage)
    store._bucket, store._prefix, store._client = BUCKET, PREFIX, client
    return store


def _read(store, method):
    return store.get(KEY, max_bytes=len(ORIGINAL)) if method == "get" else store.digest(KEY)


def _sanitized(error, expected=READ_ERROR):
    assert str(error) == expected
    assert all(value not in str(error) for value in (PRIVATE_DIAGNOSTIC, BUCKET, PREFIX, KEY))


def _closed(body, *, bounded=True):
    assert body.source.closed and body.close_calls == 1
    assert body.requests
    if bounded:
        assert all(0 < amount <= 65536 for amount in body.requests)


def _recover(store, client, path, method):
    assert path.read_bytes() == ORIGINAL
    body = _Body(path.open("rb"))
    client.failure = None
    # Neither a false length nor an ETag establishes the bytes' identity.
    client.response = {"Body": body, "ContentLength": 0, "ETag": "not-a-sha256"}
    expected = ORIGINAL if method == "get" else hashlib.sha256(ORIGINAL).hexdigest()
    assert _read(store, method) == expected
    _closed(body)
    assert body.consumed == len(ORIGINAL)
    assert client.calls and all(
        call == {"Bucket": BUCKET, "Key": PREFIX + "/" + KEY} for call in client.calls
    )


def _provider(code):
    status = 404 if code in {"NoSuchKey", "404", "NotFound", "NoSuchBucket"} else 503
    return ClientError(
        {
            "Error": {"Code": code, "Message": PRIVATE_DIAGNOSTIC},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "GetObject",
    )


@pytest.mark.parametrize("method", ["get", "digest"])
@pytest.mark.parametrize("code", ["NoSuchKey", "404", "NotFound"])
def test_true_missing_codes_are_static_and_recoverable(tmp_path, method, code):
    path = _source(tmp_path)
    failure = _provider(code)
    client = _Client(failure=failure)
    store = _store(client)
    with pytest.raises(storage.StorageError) as error:
        _read(store, method)
    assert not isinstance(error.value, storage.StorageReadError)
    _sanitized(error.value, MISSING_ERROR)
    assert error.value.__cause__ is failure
    _recover(store, client, path, method)


@pytest.mark.parametrize("method", ["get", "digest"])
@pytest.mark.parametrize("code", ["AccessDenied", "SlowDown", "NoSuchBucket", "InternalError"])
def test_provider_failures_are_read_errors_not_missing_objects(tmp_path, method, code):
    path = _source(tmp_path)
    failure = _provider(code)
    client = _Client(failure=failure)
    store = _store(client)
    with pytest.raises(storage.StorageReadError) as error:
        _read(store, method)
    _sanitized(error.value)
    assert error.value.__cause__ is failure
    _recover(store, client, path, method)


@pytest.mark.parametrize("method", ["get", "digest"])
@pytest.mark.parametrize("kind", ["endpoint", "connection"])
def test_network_lookup_failure_is_sanitized_and_recovers(tmp_path, method, kind):
    path = _source(tmp_path)
    failure = (
        EndpointConnectionError(endpoint_url=PRIVATE_DIAGNOSTIC)
        if kind == "endpoint"
        else ConnectionResetError(PRIVATE_DIAGNOSTIC)
    )
    client = _Client(failure=failure)
    store = _store(client)
    with pytest.raises(storage.StorageReadError) as error:
        _read(store, method)
    _sanitized(error.value)
    assert error.value.__cause__ is failure
    _recover(store, client, path, method)


@pytest.mark.parametrize("method", ["get", "digest"])
@pytest.mark.parametrize("response", [None, {}, {"Body": None}])
def test_malformed_provider_response_never_reports_missing_or_success(tmp_path, method, response):
    path = _source(tmp_path)
    client = _Client(response=response)
    store = _store(client)
    with pytest.raises(storage.StorageReadError) as error:
        _read(store, method)
    _sanitized(error.value)
    _recover(store, client, path, method)


@pytest.mark.parametrize("method", ["get", "digest"])
@pytest.mark.parametrize("stage", ["read", "close"])
@pytest.mark.parametrize("error_type", [OSError, FileNotFoundError, RuntimeError])
def test_post_open_exceptions_are_sanitized_read_errors_and_close_once(
    tmp_path, method, stage, error_type
):
    path = _source(tmp_path)
    failure = error_type(PRIVATE_DIAGNOSTIC)
    body = _Body(path.open("rb"), **{stage + "_error": failure})
    client = _Client(response={"Body": body, "ContentLength": 0})
    store = _store(client)
    with pytest.raises(storage.StorageReadError) as error:
        _read(store, method)
    _sanitized(error.value)
    assert error.value.__cause__ is failure
    _closed(body)
    assert body.consumed == (4093 if stage == "read" else len(ORIGINAL))
    _recover(store, client, path, method)


@pytest.mark.parametrize("method", ["get", "digest"])
@pytest.mark.parametrize("close_fails", [False, True])
def test_real_truncated_stream_retains_primary_error_and_closes(tmp_path, method, close_fails):
    path = _source(tmp_path)
    raw = _Body(path.open("rb"), close_error=OSError(PRIVATE_DIAGNOSTIC) if close_fails else None)
    body = _Stream(raw, len(ORIGINAL) + 100)
    client = _Client(response={"Body": body, "ContentLength": 0})
    store = _store(client)
    with pytest.raises(storage.StorageReadError) as error:
        _read(store, method)
    _sanitized(error.value)
    assert isinstance(error.value.__cause__, IncompleteReadError)
    assert body.close_calls == 1
    _closed(raw)
    assert raw.consumed == len(ORIGINAL)
    _recover(store, client, path, method)


@pytest.mark.parametrize("close_error", [OSError, FileNotFoundError])
def test_oversize_refusal_survives_secondary_close_io_and_stops_at_cap_plus_one(
    tmp_path, close_error
):
    path = _source(tmp_path)
    body = _Body(path.open("rb"), close_error=close_error(PRIVATE_DIAGNOSTIC))
    client = _Client(response={"Body": body, "ContentLength": 0})
    store = _store(client)
    with pytest.raises(storage.StorageReadTooLarge) as error:
        store.get(KEY, max_bytes=70000)
    _sanitized(error.value, "Stored document exceeds the processing size limit.")
    _closed(body)
    assert body.consumed == 70001
    _recover(store, client, path, "get")


@pytest.mark.parametrize("method", ["get", "digest"])
@pytest.mark.parametrize("failure_type", [OSError, RuntimeError, asyncio.CancelledError, _StopRead])
def test_primary_read_failure_survives_secondary_close_io(tmp_path, method, failure_type):
    path = _source(tmp_path)
    primary = failure_type("synthetic primary interruption")
    body = _Body(path.open("rb"), read_error=primary, close_error=OSError(PRIVATE_DIAGNOSTIC))
    client = _Client(response={"Body": body})
    store = _store(client)
    if isinstance(primary, Exception):
        with pytest.raises(storage.StorageReadError) as error:
            _read(store, method)
        _sanitized(error.value)
        assert error.value.__cause__ is primary
    else:
        with pytest.raises(failure_type) as error:
            _read(store, method)
        assert error.value is primary
    _closed(body)
    assert body.consumed == 4093
    _recover(store, client, path, method)


@pytest.mark.parametrize("failure_type", [OSError, RuntimeError, asyncio.CancelledError])
def test_primary_hash_update_failure_survives_secondary_close_io(
    tmp_path, monkeypatch, failure_type
):
    path = _source(tmp_path)
    primary = failure_type("synthetic hash interruption")
    body = _Body(path.open("rb"), close_error=OSError(PRIVATE_DIAGNOSTIC))
    client = _Client(response={"Body": body})
    store = _store(client)

    class BrokenHash:
        def update(self, data):
            raise primary

    with monkeypatch.context() as patch:
        patch.setattr(storage.hashlib, "sha256", lambda *args, **kwargs: BrokenHash())
        if isinstance(primary, Exception):
            with pytest.raises(storage.StorageReadError) as error:
                store.digest(KEY)
            _sanitized(error.value)
            assert error.value.__cause__ is primary
        else:
            with pytest.raises(failure_type) as error:
                store.digest(KEY)
            assert error.value is primary
        _closed(body)
        assert body.consumed == 4093
    _recover(store, client, path, "digest")


@pytest.mark.parametrize("limit", [None, len(ORIGINAL)])
def test_exact_boundary_and_unbounded_get_keep_original_bytes_and_close_once(tmp_path, limit):
    path = _source(tmp_path)
    body = _Body(path.open("rb"))
    client = _Client(response={"Body": body, "ContentLength": 0, "ETag": "not-a-sha256"})
    store = _store(client)
    assert store.get(KEY, max_bytes=limit) == ORIGINAL
    _closed(body, bounded=limit is not None)
    assert body.consumed == len(ORIGINAL)
    if limit is None:
        assert body.requests == [-1]
    assert path.read_bytes() == ORIGINAL
