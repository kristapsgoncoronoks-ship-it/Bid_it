"""Tests for off-machine backup sync (backup.sync_dir/sync_snapshot/last_synced and
the snapshot() auto-sync hook). Hermetic: a tmp BACKUPDIR/WORKDIR and tmp sync dir,
the real backups/ folder is never touched and no demo DBs are read."""
import importlib
import os

import pytest


@pytest.fixture()
def bk(tmp_path, monkeypatch):
    """backup module driven against an isolated tmp BACKUPDIR/WORKDIR."""
    import backup
    importlib.reload(backup)
    work = tmp_path / "app"; work.mkdir()
    monkeypatch.setattr(backup, "WORKDIR", str(work))
    monkeypatch.setattr(backup, "BACKUPDIR", str(tmp_path / "backups"))
    os.makedirs(backup.BACKUPDIR, exist_ok=True)
    # No env sync dir by default; tests opt in explicitly.
    monkeypatch.delenv("FFS_BACKUP_SYNC_DIR", raising=False)
    return backup


def _fake_zip(bk, name, data=b"zip-bytes"):
    p = os.path.join(bk.BACKUPDIR, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


# ---------------------------------------------------------------- sync_dir
def test_sync_dir_env_wins(bk, monkeypatch, tmp_path):
    d = str(tmp_path / "offsite")
    monkeypatch.setenv("FFS_BACKUP_SYNC_DIR", "  " + d + "  ")
    assert bk.sync_dir() == d                      # stripped


def test_sync_dir_disabled_by_default(bk, monkeypatch):
    # No env, auth setting empty -> "" (disabled), never raises.
    monkeypatch_setting(monkeypatch, "")
    assert bk.sync_dir() == ""


def monkeypatch_setting(monkeypatch, val):
    """Stub the lazily-imported auth so backup.sync_dir() resolves to val.
    Uses monkeypatch.setitem so the stub is restored after the test."""
    import sys, types
    stub = types.ModuleType("auth")
    stub.get_setting = lambda k, default="": (val if val else default)
    monkeypatch.setitem(sys.modules, "auth", stub)


# ---------------------------------------------------------------- sync_snapshot copy
def test_sync_snapshot_copies_newest(bk, monkeypatch, tmp_path):
    d = str(tmp_path / "offsite")
    monkeypatch.setenv("FFS_BACKUP_SYNC_DIR", d)
    src = _fake_zip(bk, "ffs_20260101_000000.zip", b"hello-bytes")
    dest = bk.sync_snapshot(src)
    assert dest == os.path.join(d, "ffs_20260101_000000.zip")
    assert open(dest, "rb").read() == b"hello-bytes"   # identical bytes


def test_sync_snapshot_defaults_to_last_snapshot(bk, monkeypatch, tmp_path):
    d = str(tmp_path / "offsite")
    monkeypatch.setenv("FFS_BACKUP_SYNC_DIR", d)
    _fake_zip(bk, "ffs_20260101_000000.zip", b"old")
    _fake_zip(bk, "ffs_20260202_000000.zip", b"new")
    dest = bk.sync_snapshot()                           # no path -> newest local
    assert os.path.basename(dest) == "ffs_20260202_000000.zip"
    assert open(dest, "rb").read() == b"new"


# ---------------------------------------------------------------- pruning
def test_sync_snapshot_prunes_to_keep(bk, monkeypatch, tmp_path):
    d = str(tmp_path / "offsite")
    os.makedirs(d, exist_ok=True)
    monkeypatch.setenv("FFS_BACKUP_SYNC_DIR", d)
    # pre-seed > KEEP fake zips directly in the sync dir
    for i in range(bk.KEEP + 5):
        with open(os.path.join(d, f"ffs_202601{i:02d}_000000.zip"), "wb") as f:
            f.write(b"x")
    # newest local zip to copy in
    src = _fake_zip(bk, "ffs_20260301_000000.zip", b"latest")
    bk.sync_snapshot(src)
    remaining = sorted(f for f in os.listdir(d) if f.startswith("ffs_"))
    assert len(remaining) == bk.KEEP                   # rotated to KEEP
    assert "ffs_20260301_000000.zip" in remaining      # newest kept


# ---------------------------------------------------------------- disabled
def test_sync_snapshot_disabled_noop(bk, monkeypatch, tmp_path):
    monkeypatch.delenv("FFS_BACKUP_SYNC_DIR", raising=False)
    monkeypatch_setting(monkeypatch, "")               # auth setting empty too
    _fake_zip(bk, "ffs_20260101_000000.zip")
    assert bk.sync_snapshot() is None                  # disabled -> None, no raise


# ---------------------------------------------------------------- failure non-fatal
def test_sync_snapshot_failure_non_fatal(bk, monkeypatch, tmp_path):
    # Point the sync dir at a path whose parent is a FILE, so makedirs cannot create it.
    afile = tmp_path / "afile"; afile.write_bytes(b"x")
    bad = str(afile / "subdir")                         # can't mkdir under a file
    monkeypatch.setenv("FFS_BACKUP_SYNC_DIR", bad)
    src = _fake_zip(bk, "ffs_20260101_000000.zip", b"data")
    assert bk.sync_snapshot(src) is None               # logged, no raise


def test_snapshot_survives_sync_failure(bk, monkeypatch, tmp_path):
    monkeypatch.setattr(bk, "DATA", [])                # no DBs to copy
    monkeypatch.setattr(bk, "EXTRA_DIRS", [])
    # one code-report file so the manifest is non-empty (proves the snapshot
    # captured content despite the off-site sync failing below).
    with open(os.path.join(bk.WORKDIR, "marker.py"), "w") as f:
        f.write("# marker\n")
    afile = tmp_path / "afile2"; afile.write_bytes(b"x")
    monkeypatch.setenv("FFS_BACKUP_SYNC_DIR", str(afile / "nope"))
    path, n = bk.snapshot()
    assert os.path.exists(path)                         # local zip written despite sync fault
    assert n >= 1
    # nothing landed off-site
    assert bk.last_synced() == (None, None) or not os.path.exists(bk.last_synced()[0] or "x")


# ---------------------------------------------------------------- auto-sync hook
def test_snapshot_auto_syncs(bk, monkeypatch, tmp_path):
    monkeypatch.setattr(bk, "DATA", [])
    monkeypatch.setattr(bk, "EXTRA_DIRS", [])
    d = str(tmp_path / "offsite")
    monkeypatch.setenv("FFS_BACKUP_SYNC_DIR", d)
    path, n = bk.snapshot()
    off = os.path.join(d, os.path.basename(path))
    assert os.path.exists(off)                          # zip landed off-site too
    assert open(off, "rb").read() == open(path, "rb").read()
    ls_path, ls_mtime = bk.last_synced()
    assert ls_path == off and ls_mtime is not None      # last_synced reflects it


# ---------------------------------------------------------------- last_synced disabled
def test_last_synced_disabled(bk, monkeypatch):
    monkeypatch.delenv("FFS_BACKUP_SYNC_DIR", raising=False)
    monkeypatch_setting(monkeypatch, "")
    assert bk.last_synced() == (None, None)
