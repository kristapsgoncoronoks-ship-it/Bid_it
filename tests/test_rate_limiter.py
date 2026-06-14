"""Tests for the OPT-IN per-supplier rate-limit / concurrency-cap / min-interval /
circuit-breaker primitive on the intake queue (waiting_room.py).

The cardinal property under test is ZERO REGRESSION: a supplier with NO configured
limit row is UNGOVERNED and behaves exactly as today (unlimited). Time is controlled
via monkeypatch (no real sleeps) for determinism."""
import importlib

import pytest


@pytest.fixture()
def iq(tmp_path, monkeypatch):
    import waiting_room
    importlib.reload(waiting_room)
    monkeypatch.setattr(waiting_room, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(waiting_room, "INBOX", str(tmp_path / "inbox"))
    waiting_room._SCHEMA_READY.clear()
    return waiting_room


def _enqueue(iq, marker, backend=None):
    """Enqueue a distinct extract job; the marker keeps content hashes unique."""
    jid, _ = iq.enqueue(b"%PDF-1.4 " + marker.encode(), f"{marker}.pdf", backend=backend)
    return jid


# ---------------------------------------------------------------- zero regression
def test_ungoverned_supplier_is_unlimited(iq):
    """No limit row configured -> _claim returns every job ungated, in id order."""
    a = _enqueue(iq, "a", backend="UNGOV")
    b = _enqueue(iq, "b", backend="UNGOV")
    c = _enqueue(iq, "c", backend=None)
    claimed = [iq._claim(iq_con) for iq_con in (iq.connect(),)]
    # claim three in a row via a fresh connection each time (mirrors process_one)
    got = []
    for _ in range(3):
        con = iq.connect()
        row = iq._claim(con)
        con.close()
        if row:
            got.append(row["id"])
    # the first claim above already took id `a`; collect all four claim attempts
    got = [r["id"] for r in claimed if r] + got
    assert got[:3] == [a, b, c]


def test_no_limits_means_no_blocked_set(iq):
    con = iq.connect()
    try:
        assert iq._blocked_suppliers(con, iq._now()) == set()
    finally:
        con.close()


# ---------------------------------------------------------------- concurrency cap
def test_concurrency_cap(iq):
    iq.set_supplier_limit("X", max_concurrent=1, min_interval_s=0)
    x1 = _enqueue(iq, "x1", backend="X")
    x2 = _enqueue(iq, "x2", backend="X")
    other = _enqueue(iq, "o", backend="UNGOV")

    con = iq.connect()
    r1 = iq._claim(con); con.close()
    assert r1["id"] == x1
    assert iq.get_job(x1)["status"] == "processing"

    # X is now at its cap (1 in-flight) -> the 2nd X job is skipped, but the
    # ungoverned job still surfaces.
    con = iq.connect()
    r2 = iq._claim(con); con.close()
    assert r2["id"] == other

    # nothing else claimable while X is capped and the ungoverned job is in-flight
    con = iq.connect()
    r3 = iq._claim(con); con.close()
    assert r3 is None

    # complete the first X job (status flips off 'processing') -> x2 becomes claimable
    iq.complete(x1)
    con = iq.connect()
    r4 = iq._claim(con); con.close()
    assert r4["id"] == x2


# ---------------------------------------------------------------- min-interval
def test_min_interval_spacing(iq, monkeypatch):
    iq.set_supplier_limit("X", max_concurrent=5, min_interval_s=100.0)
    x1 = _enqueue(iq, "x1", backend="X")
    x2 = _enqueue(iq, "x2", backend="X")

    base = 1_000_000.0
    monkeypatch.setattr(iq.time, "time", lambda: base)
    monkeypatch.setattr(iq, "_now", lambda: iq._at(base))

    con = iq.connect()
    r1 = iq._claim(con); con.close()
    assert r1["id"] == x1                       # stamps last_start_at = now

    # immediately after: within the 100s interval -> x2 is spaced out (skipped)
    con = iq.connect()
    r2 = iq._claim(con); con.close()
    assert r2 is None

    # advance time past the interval -> x2 becomes claimable
    later = base + 101.0
    monkeypatch.setattr(iq.time, "time", lambda: later)
    monkeypatch.setattr(iq, "_now", lambda: iq._at(later))
    con = iq.connect()
    r3 = iq._claim(con); con.close()
    assert r3["id"] == x2


# ---------------------------------------------------------------- circuit-breaker
def test_circuit_breaker_opens_and_recovers(iq, monkeypatch):
    iq.set_supplier_limit("X", max_concurrent=5, min_interval_s=0,
                          breaker_threshold=3, breaker_cooldown_s=300.0)
    base = 2_000_000.0
    monkeypatch.setattr(iq.time, "time", lambda: base)
    monkeypatch.setattr(iq, "_now", lambda: iq._at(base))

    x1 = _enqueue(iq, "x1", backend="X")

    # two failures: breaker still closed
    iq.record_outcome("X", ok=False)
    iq.record_outcome("X", ok=False)
    assert iq.breaker_state("X")["open"] is False
    assert iq.breaker_state("X")["consec_failures"] == 2

    # third failure trips the breaker
    iq.record_outcome("X", ok=False)
    bs = iq.breaker_state("X")
    assert bs["open"] is True and bs["consec_failures"] == 3

    # while open, X jobs are not claimed
    con = iq.connect()
    r = iq._claim(con); con.close()
    assert r is None

    # advance past cooldown -> breaker no longer open, X claimable again
    later = base + 301.0
    monkeypatch.setattr(iq.time, "time", lambda: later)
    monkeypatch.setattr(iq, "_now", lambda: iq._at(later))
    assert iq.breaker_state("X")["open"] is False
    con = iq.connect()
    r = iq._claim(con); con.close()
    assert r["id"] == x1


def test_record_outcome_success_resets(iq):
    iq.set_supplier_limit("X", breaker_threshold=3)
    iq.record_outcome("X", ok=False)
    iq.record_outcome("X", ok=False)
    assert iq.breaker_state("X")["consec_failures"] == 2
    iq.record_outcome("X", ok=True)
    bs = iq.breaker_state("X")
    assert bs["consec_failures"] == 0 and bs["open"] is False


def test_record_outcome_ungoverned_is_noop(iq):
    # never raises, never creates state for an ungoverned supplier
    iq.record_outcome("UNGOV", ok=False)
    iq.record_outcome("UNGOV", ok=True)
    assert iq.breaker_state("UNGOV") == {"open": False, "open_until": None,
                                         "consec_failures": 0}


# ---------------------------------------------------------------- config API
def test_config_roundtrip_and_defaults(iq):
    # None fields fall back to DEFAULT_* constants
    resolved = iq.set_supplier_limit("X")
    assert resolved["max_concurrent"] == iq.DEFAULT_MAX_CONCURRENT
    assert resolved["min_interval_s"] == iq.DEFAULT_MIN_INTERVAL_S
    assert resolved["breaker_threshold"] == iq.DEFAULT_BREAKER_THRESHOLD
    assert resolved["breaker_cooldown_s"] == iq.DEFAULT_BREAKER_COOLDOWN_S
    assert resolved["enabled"] == 1

    got = iq.get_supplier_limit("X")
    assert got["max_concurrent"] == iq.DEFAULT_MAX_CONCURRENT
    assert got["enabled"] == 1

    # explicit values upsert (update in place, not duplicate)
    iq.set_supplier_limit("X", max_concurrent=7, min_interval_s=12.5,
                          breaker_threshold=9, breaker_cooldown_s=42.0, enabled=False)
    got = iq.get_supplier_limit("X")
    assert got["max_concurrent"] == 7 and got["min_interval_s"] == 12.5
    assert got["breaker_threshold"] == 9 and got["breaker_cooldown_s"] == 42.0
    assert got["enabled"] == 0

    # disabled supplier is NOT governed (not in the blocked computation source)
    assert iq.get_supplier_limit("UNGOV") is None

    rows = iq.list_supplier_limits()
    assert len(rows) == 1 and rows[0]["supplier"] == "X"
    assert "consec_failures" in rows[0]          # joined with state

    assert iq.clear_supplier_limit("X") is True
    assert iq.get_supplier_limit("X") is None
    assert iq.list_supplier_limits() == []
    assert iq.clear_supplier_limit("X") is False  # idempotent


def test_disabled_limit_is_ungoverned(iq):
    """An enabled=0 row must behave like unlimited (not in the blocked set)."""
    iq.set_supplier_limit("X", max_concurrent=1, enabled=False)
    x1 = _enqueue(iq, "x1", backend="X")
    x2 = _enqueue(iq, "x2", backend="X")
    con = iq.connect()
    assert iq._blocked_suppliers(con, iq._now()) == set()
    con.close()
    con = iq.connect(); r1 = iq._claim(con); con.close()
    assert r1["id"] == x1
    # even with one in-flight, a DISABLED limit does not cap -> x2 still claims
    con = iq.connect(); r2 = iq._claim(con); con.close()
    assert r2["id"] == x2
