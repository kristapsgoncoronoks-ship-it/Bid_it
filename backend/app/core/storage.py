"""Object storage abstraction for document bytes (ADR-0008).

Large binary originals (receipts, logos, email attachments) belong in object
storage, not the primary database — see docs/architecture/adr/0008-object-storage.md.
This module is the swappable seam: a tiny `Storage` protocol with three backends:

  • MemoryStorage — in-process dict; used by tests (no disk, isolated).
  • LocalStorage  — filesystem under a root; the default for dev/single-node.
  • S3Storage     — any S3-compatible service (AWS S3, MinIO); the prod target.

Keys are CONTENT-ADDRESSED (sha256), sharded, and tenant-prefixed, which gives
deduplication and integrity for free. The backend is chosen from settings and
cached per process; tests swap it via `set_storage`.

The methods are synchronous (filesystem/boto3 are blocking); callers on the async
path wrap them via `services.documents` which uses a threadpool.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.core.config import settings


class StorageError(Exception):
    """Raised when a stored object cannot be read/written (missing key, backend error)."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_key(prefix: str, org_id: str, sha256: str) -> str:
    """Tenant-prefixed, sharded, content-addressed key.

    e.g. ``receipts/<org>/ab/cd/<sha256>`` — the two shard levels keep any single
    directory / prefix from growing unbounded.
    """
    return f"{prefix}/{org_id}/{sha256[:2]}/{sha256[2:4]}/{sha256}"


@runtime_checkable
class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class MemoryStorage:
    """In-process store for tests. Not durable; not for production."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        self._data[key] = bytes(data)

    def get(self, key: str) -> bytes:
        try:
            return self._data[key]
        except KeyError as e:
            raise StorageError(f"object not found: {key}") from e

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self._data


class LocalStorage:
    """Filesystem-backed storage under a root directory (dev / single node)."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Keys never contain '..'; they are derived from sha256 + org id.
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise StorageError(f"illegal key path: {key}")
        return p

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)  # atomic within the same filesystem

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as e:
            raise StorageError(f"object not found: {key}") from e

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Storage:
    """S3-compatible object storage (AWS S3, MinIO). boto3 imported lazily so
    dev/test installs without it still work."""

    def __init__(self, bucket: str, *, endpoint_url: str | None = None,
                 region: str | None = None, prefix: str = "") -> None:
        try:
            import boto3  # noqa: PLC0415 — lazy: only needed for the s3 backend
        except ImportError as e:  # pragma: no cover
            raise StorageError("boto3 is required for the s3 storage backend") from e
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        # Credentials come from the standard AWS env/instance chain.
        self._client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)

    def _k(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else {}
        self._client.put_object(Bucket=self._bucket, Key=self._k(key), Body=data, **extra)

    def get(self, key: str) -> bytes:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=self._k(key))
            return resp["Body"].read()
        except Exception as e:  # noqa: BLE001 — normalise all boto errors
            raise StorageError(f"object not found: {key}") from e

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=self._k(key))

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=self._k(key))
            return True
        except Exception:  # noqa: BLE001
            return False


# --- process-wide singleton (swappable in tests) -------------------------------

_storage: Storage | None = None


def _build_from_settings() -> Storage:
    backend = (settings.storage_backend or "local").lower()
    if backend == "s3":
        return S3Storage(
            settings.storage_s3_bucket,
            endpoint_url=settings.storage_s3_endpoint_url or None,
            region=settings.storage_s3_region or None,
            prefix=settings.storage_s3_prefix or "",
        )
    if backend == "memory":
        return MemoryStorage()
    return LocalStorage(settings.storage_local_path)


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = _build_from_settings()
    return _storage


def set_storage(storage: Storage) -> None:
    """Override the backend (tests)."""
    global _storage
    _storage = storage


def reset_storage() -> None:
    """Clear the cached backend so the next `get_storage` rebuilds from settings."""
    global _storage
    _storage = None
