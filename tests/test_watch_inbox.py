"""
P1-A — the inbox watcher feeds the DURABLE intake queue (waiting_room.enqueue),
not an orphan review_queue/ that nothing reads.

We monkeypatch waiting_room.enqueue with a recorder so no real extraction/AI runs
(enqueue itself never extracts), drop files into a temp WATCH_INBOX_DIR, and assert:
  * each new file is enqueued (including a content-duplicate — enqueue dedupes),
  * processed files move to the DONE subdir, and
  * an unreadable file is LOGGED, not raised.
"""
import os
import importlib


def _fresh_watcher(tmp_path, monkeypatch):
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setenv("WATCH_INBOX_DIR", str(drop))
    import watch_inbox
    importlib.reload(watch_inbox)        # re-read WATCH_INBOX_DIR at import time
    return watch_inbox, drop


def test_pass_once_enqueues_and_moves(tmp_path, monkeypatch):
    watch_inbox, drop = _fresh_watcher(tmp_path, monkeypatch)

    calls = []
    import waiting_room
    monkeypatch.setattr(waiting_room, "enqueue",
                        lambda data, fn, user="system": (calls.append((fn, user, data)),
                                                         (len(calls), "queued"))[1])

    (drop / "a.pdf").write_bytes(b"%PDF-1.4 one")
    (drop / "b.pdf").write_bytes(b"%PDF-1.4 one")   # same bytes -> a content duplicate
    (drop / "ignore.txt").write_text("not an invoice")

    n = watch_inbox.pass_once()

    assert n == 2                                    # both PDFs enqueued, .txt skipped
    assert {c[0] for c in calls} == {"a.pdf", "b.pdf"}
    assert all(c[1] == "inbox-watcher" for c in calls)
    # processed files moved out of the scan dir into DONE
    assert not (drop / "a.pdf").exists()
    assert not (drop / "b.pdf").exists()
    assert (drop / "processed" / "a.pdf").exists()
    assert (drop / "processed" / "b.pdf").exists()
    # the non-invoice file is left untouched (never enqueued)
    assert (drop / "ignore.txt").exists()


def test_bad_file_is_logged_not_raised(tmp_path, monkeypatch):
    watch_inbox, drop = _fresh_watcher(tmp_path, monkeypatch)

    import waiting_room
    monkeypatch.setattr(waiting_room, "enqueue",
                        lambda data, fn, user="system": (1, "queued"))

    (drop / "boom.pdf").write_bytes(b"%PDF-1.4 boom")

    # make reading the file blow up
    real_open = open

    def exploding_open(path, *a, **k):
        if str(path).endswith("boom.pdf") and "b" in (a[0] if a else k.get("mode", "")):
            raise OSError("disk gremlin")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", exploding_open)

    warnings = []
    monkeypatch.setattr(watch_inbox.log, "warning",
                        lambda *a, **k: warnings.append((a, k)))

    # must NOT raise
    n = watch_inbox.pass_once()

    assert n == 0
    assert warnings, "the unreadable file should have been logged"
    # the bad file stays in the scan dir (not moved on failure)
    assert (drop / "boom.pdf").exists()
