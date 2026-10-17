"""Real filesystem faults and concurrent writes must preserve original objects."""

import errno
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.core.storage import LocalStorage


def test_concurrent_same_object_writes_both_succeed(tmp_path, monkeypatch):
    store = LocalStorage(str(tmp_path))
    barrier = threading.Barrier(2)
    original_replace = Path.replace

    def simultaneous_replace(source, target):
        barrier.wait(timeout=5)
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", simultaneous_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [
            pool.submit(store.put, "uploads/synthetic/object", b"same original") for _ in range(2)
        ]
        for call in calls:
            call.result(timeout=10)
    assert store.get("uploads/synthetic/object") == b"same original"
    assert sorted(p.name for p in (tmp_path / "uploads/synthetic").iterdir()) == ["object"]


def test_failed_atomic_replace_preserves_original_and_cleans_own_temp(tmp_path, monkeypatch):
    store = LocalStorage(str(tmp_path))
    store.put("uploads/synthetic/object", b"original")

    def disk_failure(*args):
        raise OSError(errno.ENOSPC, "synthetic exhausted disk")

    monkeypatch.setattr(Path, "replace", disk_failure)
    with pytest.raises(OSError):
        store.put("uploads/synthetic/object", b"replacement")
    assert store.get("uploads/synthetic/object") == b"original"
    assert sorted(p.name for p in (tmp_path / "uploads/synthetic").iterdir()) == ["object"]
