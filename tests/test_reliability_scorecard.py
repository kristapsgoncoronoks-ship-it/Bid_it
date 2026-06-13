"""Tests for waiting_room.reliability_scorecard — the per-channel processing-reliability
scorecard (success/retry rate, durations, top failure reason) and the overall
failure-reason histogram. Read-only analytics over intake_jobs; must never raise."""
import importlib
import json
import time

import pytest


@pytest.fixture()
def iq(tmp_path, monkeypatch):
    import waiting_room
    importlib.reload(waiting_room)
    monkeypatch.setattr(waiting_room, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(waiting_room, "INBOX", str(tmp_path / "inbox"))
    waiting_room._SCHEMA_READY.clear()
    return waiting_room


def _insert(iq, status, backend=None, attempts=0, started_at=None, finished_at=None,
            error=None, draft=None, sha=None):
    """Insert a bare intake_jobs row directly; returns its id."""
    sha = sha or f"sha-{status}-{backend}-{time.time_ns()}"
    con = iq.connect()
    cur = con.execute(
        "INSERT INTO intake_jobs (sha256, filename, status, backend, attempts, "
        "started_at, finished_at, error, draft) VALUES (?,?,?,?,?,?,?,?,?)",
        (sha, f"{status}.pdf", status, backend, attempts, started_at, finished_at,
         error, draft))
    con.commit()
    jid = cur.lastrowid
    con.close()
    return jid


def _channel(sc, name):
    for c in sc["channels"]:
        if c["channel"] == name:
            return c
    raise AssertionError(f"channel {name!r} not in {[c['channel'] for c in sc['channels']]}")


# --------------------------------------------------------------- empty / safety
def test_empty_queue_is_safe(iq):
    sc = iq.reliability_scorecard()
    assert sc == {"channels": [], "failure_reasons": [], "suppliers": []}


def test_never_raises_on_malformed_timestamps_and_draft(iq):
    _insert(iq, "done", backend="ai", started_at="not-a-time", finished_at="also-bad")
    _insert(iq, "ready", backend="ai", draft="{not valid json")
    _insert(iq, "failed", backend="ai", finished_at="2026-01-01 00:00:00")  # no started_at
    sc = iq.reliability_scorecard()                # must not raise
    ch = _channel(sc, "ai")
    assert ch["total"] == 3
    assert ch["duration_n"] == 0                   # no row had two parseable timestamps
    assert ch["median_duration_s"] is None and ch["avg_duration_s"] is None


# --------------------------------------------------------------- counts / split
def test_per_channel_counts_and_null_backend_is_auto(iq):
    _insert(iq, "done", backend="parser")
    _insert(iq, "failed", backend="parser")
    _insert(iq, "queued", backend="parser")        # pending
    _insert(iq, "done", backend=None)              # NULL backend -> 'auto'
    sc = iq.reliability_scorecard()
    p = _channel(sc, "parser")
    assert p["total"] == 3
    assert p["done"] == 1 and p["failed"] == 1 and p["pending"] == 1
    a = _channel(sc, "auto")
    assert a["total"] == 1 and a["done"] == 1


def test_success_rate_terminal_only(iq):
    # 3 done, 1 failed, 1 held => terminal=5, success=3/5=0.6; pending ignored.
    for _ in range(3):
        _insert(iq, "done", backend="ai")
    _insert(iq, "failed", backend="ai", error="RuntimeError: boom")
    _insert(iq, "held", backend="ai", error="held after retries")
    _insert(iq, "queued", backend="ai")            # pending — excluded from rate
    ch = _channel(iq.reliability_scorecard(), "ai")
    assert ch["success_rate"] == pytest.approx(0.6)
    assert ch["pending"] == 1


def test_success_rate_none_when_no_terminal_jobs(iq):
    _insert(iq, "queued", backend="ai")
    _insert(iq, "processing", backend="ai")
    ch = _channel(iq.reliability_scorecard(), "ai")
    assert ch["success_rate"] is None
    assert ch["pending"] == 2


# --------------------------------------------------------------- retry rate
def test_retry_rate_reflects_attempts_gt_one(iq):
    _insert(iq, "done", backend="ai", attempts=1)   # not a retry
    _insert(iq, "done", backend="ai", attempts=3)   # retried
    _insert(iq, "failed", backend="ai", attempts=5, error="X: y")  # retried
    _insert(iq, "queued", backend="ai", attempts=0) # not a retry
    ch = _channel(iq.reliability_scorecard(), "ai")
    assert ch["total"] == 4
    assert ch["retry_rate"] == pytest.approx(2 / 4)


# --------------------------------------------------------------- durations
def test_durations_median_and_avg(iq):
    base = time.time()
    # three durations: 10s, 20s, 30s -> median 20, avg 20
    for d in (10, 20, 30):
        s = iq._at(base)
        f = iq._at(base + d)
        _insert(iq, "done", backend="ai", started_at=s, finished_at=f)
    # a row missing finished_at must be excluded, not crash
    _insert(iq, "processing", backend="ai", started_at=iq._at(base))
    ch = _channel(iq.reliability_scorecard(), "ai")
    assert ch["duration_n"] == 3
    assert ch["median_duration_s"] == pytest.approx(20)
    assert ch["avg_duration_s"] == pytest.approx(20)


def test_duration_median_even_count(iq):
    base = time.time()
    for d in (10, 20, 30, 40):
        _insert(iq, "done", backend="ai",
                started_at=iq._at(base), finished_at=iq._at(base + d))
    ch = _channel(iq.reliability_scorecard(), "ai")
    assert ch["duration_n"] == 4
    assert ch["median_duration_s"] == pytest.approx(25)   # (20+30)/2


# --------------------------------------------------------------- failure reasons
def test_failure_reasons_histogram_counts_and_order(iq):
    _insert(iq, "failed", backend="ai", error="RuntimeError: extract blew up")
    _insert(iq, "failed", backend="ai", error="RuntimeError: extract blew up again")
    _insert(iq, "failed", backend="parser", error="ValueError: bad number")
    _insert(iq, "held", backend="ai", error="held after 6 token-quota retries")
    _insert(iq, "done", backend="ai")              # not a failure — excluded
    sc = iq.reliability_scorecard()
    fr = dict(sc["failure_reasons"])
    # normalised key = "ExceptionType: detail" trimmed to 60 chars; the two
    # RuntimeError lines differ past the colon so they are distinct keys here.
    assert sum(fr.values()) == 4                   # 3 failed + 1 held
    # histogram is sorted desc by count then key
    counts = [c for _r, c in sc["failure_reasons"]]
    assert counts == sorted(counts, reverse=True)


def test_top_error_per_channel(iq):
    _insert(iq, "failed", backend="ai", error="RuntimeError: boom")
    _insert(iq, "failed", backend="ai", error="RuntimeError: boom")
    _insert(iq, "failed", backend="ai", error="ValueError: nope")
    ch = _channel(iq.reliability_scorecard(), "ai")
    assert ch["top_error"] == "RuntimeError: boom"   # most common on this channel


def test_no_failures_means_empty_top_error_and_histogram(iq):
    _insert(iq, "done", backend="ai")
    _insert(iq, "ready", backend="ai")
    sc = iq.reliability_scorecard()
    assert sc["failure_reasons"] == []
    assert _channel(sc, "ai")["top_error"] == ""


# --------------------------------------------------------------- suppliers
def test_supplier_breakdown_from_draft(iq):
    _insert(iq, "ready", backend="ai", draft=json.dumps({"supplier": "Neste"}))
    _insert(iq, "done", backend="ai", draft=json.dumps({"supplier": "Neste"}))
    _insert(iq, "ready", backend="ai", draft=json.dumps({"supplier": "Circle K"}))
    _insert(iq, "failed", backend="ai", error="X: y")   # no supplier -> not counted
    sc = iq.reliability_scorecard()
    by = {s["supplier"]: s for s in sc["suppliers"]}
    assert by["Neste"]["ready"] == 1 and by["Neste"]["done"] == 1
    assert by["Neste"]["total_resolved"] == 2
    assert by["Circle K"]["total_resolved"] == 1
    # busiest supplier first
    assert sc["suppliers"][0]["supplier"] == "Neste"


def test_channels_sorted_busiest_first(iq):
    for _ in range(3):
        _insert(iq, "done", backend="ai")
    _insert(iq, "done", backend="parser")
    sc = iq.reliability_scorecard()
    assert [c["channel"] for c in sc["channels"]] == ["ai", "parser"]
