"""Data-import log: append-only record of import outcomes, filterable for reporting."""
import importlib

import pytest


@pytest.fixture()
def il(tmp_path, monkeypatch):
    import import_log
    importlib.reload(import_log)
    monkeypatch.setattr(import_log, "DB", str(tmp_path / "import_log.db"))
    import_log._READY.clear()
    return import_log


def test_log_and_recent(il):
    il.log("upload", "a.pdf", "received", actor="amy", client="JUP", supplier="DKV", bytes=10)
    il.log("extract", "a.pdf", "success", actor="amy", supplier="DKV", records=3)
    il.log("statement", "S1", "failed", actor="amy", supplier="DKV", message="blocked")
    rows = il.recent()
    assert len(rows) == 3 and rows[0]["status"] == "failed"      # newest first
    assert il.recent(status="success")[0]["records"] == 3
    assert len(il.recent(client="JUP")) == 1
    assert len(il.recent(channel="extract")) == 1


def test_summary_counts(il):
    for s in ("received", "success", "success", "failed"):
        il.log("upload", "x", s)
    summ = il.summary(30)
    assert summ["success"] == 2 and summ["failed"] == 1 and summ["received"] == 1


def test_filters_distinct(il):
    il.log("upload", "x", "received", client="JUP", supplier="DKV")
    il.log("upload", "y", "received", client="OMUSS", supplier="BP")
    f = il.filters()
    assert set(f["clients"]) == {"JUP", "OMUSS"} and "DKV" in f["suppliers"]


def test_logging_never_raises(il, monkeypatch):
    # a logging failure must not propagate (it must never break an import)
    monkeypatch.setattr(il, "connect", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert il.log("upload", "x", "received") is None
