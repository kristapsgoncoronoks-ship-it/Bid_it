"""Tests for the intake-queue reliability telemetry (waiting_room.queue_health):
DLQ size (terminal failed/held jobs needing a human redrive), the oldest-pending-job
age SLO (a stalled/starved-worker alarm), and the sampled DLQ growth-rate history."""
import datetime
import itertools
import importlib
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


_sha_counter = itertools.count()


def _insert(iq, status, uploaded_at=None, sha=None):
    """Insert a bare intake_jobs row directly (no extractor needed); returns its id."""
    sha = sha or f"sha-{status}-{next(_sha_counter)}"
    con = iq.connect()
    cur = con.execute(
        "INSERT INTO intake_jobs (sha256, filename, status, uploaded_at) VALUES (?,?,?,?)",
        (sha, f"{status}.pdf", status, uploaded_at or iq._now()))
    con.commit()
    jid = cur.lastrowid
    con.close()
    return jid


def test_empty_queue_is_healthy(iq):
    h = iq.queue_health()
    assert h["failed"] == 0 and h["held"] == 0 and h["dlq"] == 0
    assert h["oldest_pending_age_s"] is None
    assert h["oldest_pending_id"] is None
    assert h["dlq_growth_24h"] == 0
    assert h["dlq_breach"] is False
    assert h["age_breach"] is False


def test_dlq_counts_failed_and_held(iq):
    _insert(iq, "failed")
    _insert(iq, "held")
    _insert(iq, "queued")            # not terminal -> not part of the DLQ
    h = iq.queue_health()
    assert h["failed"] == 1 and h["held"] == 1
    assert h["dlq"] == 2
    assert h["dlq_breach"] is True


def test_age_breach_when_oldest_pending_exceeds_slo(iq):
    # a queued job older than the SLO trips age_breach; a fresh one does not.
    old = iq._at(time.time() - (iq.OLDEST_PENDING_SLO_HOURS + 1) * 3600)
    jid_old = _insert(iq, "queued", uploaded_at=old)
    h = iq.queue_health()
    assert h["age_breach"] is True
    assert h["oldest_pending_id"] == jid_old
    assert h["oldest_pending_age_s"] >= iq.OLDEST_PENDING_SLO_HOURS * 3600


def test_no_age_breach_for_fresh_pending_job(iq):
    _insert(iq, "queued")           # uploaded_at defaults to now
    h = iq.queue_health()
    assert h["age_breach"] is False
    assert h["oldest_pending_age_s"] is not None
    assert h["oldest_pending_age_s"] < iq.OLDEST_PENDING_SLO_HOURS * 3600


def test_terminal_jobs_do_not_count_toward_age_slo(iq):
    # an ancient failed/held job is in the DLQ, not the "still-flowing" set, so it must
    # NOT trip the oldest-pending age alarm (only BLOCKING_STATES rows do).
    ancient = iq._at(time.time() - 1000 * 3600)
    _insert(iq, "failed", uploaded_at=ancient)
    _insert(iq, "held", uploaded_at=ancient)
    h = iq.queue_health()
    assert h["dlq"] == 2 and h["dlq_breach"] is True
    assert h["oldest_pending_age_s"] is None
    assert h["age_breach"] is False


def test_record_health_sample_and_dlq_growth(iq):
    # sample at dlq=0, then raise the DLQ, then sample again: growth reflects the rise.
    assert iq.record_health_sample() == 0
    assert iq.dlq_growth(24) == 0
    _insert(iq, "failed")
    _insert(iq, "failed")
    assert iq.record_health_sample() == 2
    assert iq.dlq_growth(24) == 2
    # queue_health surfaces the same growth figure
    h = iq.queue_health()
    assert h["dlq"] == 2 and h["dlq_growth_24h"] == 2


def test_dlq_growth_ignores_samples_outside_window(iq):
    # a stale baseline sample older than the window is excluded, so growth is 0.
    con = iq.connect()
    old_ts = iq._at(time.time() - 48 * 3600)
    con.execute("INSERT INTO intake_health_samples (ts, dlq) VALUES (?, ?)", (old_ts, 0))
    con.commit()
    con.close()
    _insert(iq, "failed")
    assert iq.dlq_growth(24) == 0          # no in-window sample -> 0
    iq.record_health_sample()              # in-window baseline at dlq=1
    assert iq.dlq_growth(24) == 0          # current==baseline -> no growth


def test_record_health_sample_prunes_old_rows(iq):
    con = iq.connect()
    very_old = iq._at(time.time() - 10 * 86400)     # older than the ~7-day retention
    con.execute("INSERT INTO intake_health_samples (ts, dlq) VALUES (?, ?)", (very_old, 5))
    con.commit()
    con.close()
    iq.record_health_sample()
    con = iq.connect()
    stale = con.execute("SELECT COUNT(*) FROM intake_health_samples WHERE ts=?",
                        (very_old,)).fetchone()[0]
    con.close()
    assert stale == 0


def test_queue_health_never_raises_on_bad_uploaded_at(iq):
    # a row with an unparseable uploaded_at must not blow up queue_health; the age is
    # simply None (and other fields still compute).
    _insert(iq, "queued", uploaded_at="not-a-timestamp")
    h = iq.queue_health()
    assert h["oldest_pending_age_s"] is None
    assert h["age_breach"] is False
