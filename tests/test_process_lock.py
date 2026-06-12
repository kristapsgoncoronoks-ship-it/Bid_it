"""Cross-process advisory lock: mutual exclusion, lease expiry, renewal, release."""
import importlib
import time


def _fresh(tmp_path):
    import process_lock
    importlib.reload(process_lock)
    process_lock.DB = str(tmp_path / "locks.db")
    process_lock._READY.clear()
    return process_lock


def test_mutual_exclusion(tmp_path):
    pl = _fresh(tmp_path)
    assert pl.acquire("job", 60, "A") is True
    assert pl.acquire("job", 60, "B") is False     # B cannot steal A's live lease
    assert pl.held_by("job") == "A"


def test_renew_same_holder(tmp_path):
    pl = _fresh(tmp_path)
    assert pl.acquire("job", 60, "A") is True
    assert pl.acquire("job", 60, "A") is True       # renewing your own lease is fine


def test_expired_lease_is_free(tmp_path):
    pl = _fresh(tmp_path)
    assert pl.acquire("job", 0.05, "A") is True
    time.sleep(0.08)
    assert pl.held_by("job") is None                # expired -> nobody holds it
    assert pl.acquire("job", 60, "B") is True        # B may now take it


def test_release(tmp_path):
    pl = _fresh(tmp_path)
    pl.acquire("job", 60, "A")
    pl.release("job", "B")                            # wrong holder: no-op
    assert pl.held_by("job") == "A"
    pl.release("job", "A")
    assert pl.held_by("job") is None
    assert pl.acquire("job", 60, "B") is True
