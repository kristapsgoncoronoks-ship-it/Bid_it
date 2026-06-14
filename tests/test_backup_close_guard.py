"""Guard tests: a backup must DEFER to an in-progress monthly close so it can't
capture a torn cross-DB snapshot (engine_close holds the "close-run" lease while it
writes the product DBs, backup.snapshot() copies several DBs under one MANIFEST).

The manual `/admin` "Run backup now" path RAISES (admin sees a banner); the scheduled
`_backup_tick` path returns None SILENTLY (a deferred scheduled backup is normal).
"""
import pytest

import app
import process_lock


@pytest.fixture()
def fake_snapshot(monkeypatch):
    """Replace backup.snapshot with a recorder so no real backup is written."""
    import backup
    calls = []

    def _snap(*a, **k):
        calls.append((a, k))
        return ("/tmp/fake_ffs.zip", 7)

    monkeypatch.setattr(backup, "snapshot", _snap)
    return calls


def test_close_lock_matches_engine(_=None):
    """Drift guard: renaming engine_close.LOCK_NAME must not silently bypass the
    guard — app._CLOSE_LOCK has to track it."""
    import engine_close
    assert app._CLOSE_LOCK == engine_close.LOCK_NAME


def test_run_backup_now_defers_to_close(monkeypatch, fake_snapshot):
    """A close in progress -> run_backup_now() RAISES the deferral RuntimeError,
    backup.snapshot is NOT called, and the "backup-run" lock is left released."""
    monkeypatch.setattr(process_lock, "held_by",
                        lambda name: "host:999" if name == app._CLOSE_LOCK else None)

    with pytest.raises(RuntimeError, match="monthly close is in progress"):
        app.run_backup_now()

    assert fake_snapshot == []                          # never snapshotted mid-close
    assert process_lock.held_by("backup-run") is None   # lock cleanly released on raise


def test_backup_tick_silent_when_close_running(monkeypatch, fake_snapshot):
    """A scheduled tick during a close returns None silently — NOT routed through
    _auth.log_error (a deferred scheduled backup is normal, not an error)."""
    monkeypatch.setattr(process_lock, "held_by",
                        lambda name: "host:999" if name == app._CLOSE_LOCK else None)

    logged = []
    monkeypatch.setattr(app._auth, "log_error",
                        lambda *a, **k: logged.append((a, k)))

    assert app._backup_tick() is None
    assert logged == []                                 # no error logged
    assert fake_snapshot == []                          # no snapshot attempted


def test_run_backup_now_normal_path(monkeypatch, fake_snapshot):
    """No close held -> snapshot proceeds normally and returns the snapshot tuple."""
    monkeypatch.setattr(process_lock, "held_by", lambda name: None)

    path, n = app.run_backup_now()
    assert (path, n) == ("/tmp/fake_ffs.zip", 7)
    assert len(fake_snapshot) == 1                       # snapshot was taken once
    assert process_lock.held_by("backup-run") is None   # lock released after


def test_backup_tick_normal_path(monkeypatch, fake_snapshot):
    """No close held + a backup due -> tick snapshots and returns the path."""
    monkeypatch.setattr(process_lock, "held_by", lambda name: None)
    monkeypatch.setattr(app, "backup_interval_hours", lambda: 24.0)
    import backup
    monkeypatch.setattr(backup, "due", lambda hrs: True)

    assert app._backup_tick() == "/tmp/fake_ffs.zip"
    assert len(fake_snapshot) == 1
