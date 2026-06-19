"""Opt-in DAILY ECB rate-refresh SCHEDULER.

Default install: fx_refresh_interval_hours = 0 (OFF) -> the worker tick is a no-op,
byte-identical to today's manual-only /fx refresh. When an admin sets interval>0 the
sweep is due if now-last_run >= interval, exactly ONE worker fetches per due window
(it rides the notify-scheduler's single elected leader), and a fetch failure is logged
and left DUE for the next tick (never crashes the worker).

All network is mocked — ecb_rates.fetch_and_store is monkeypatched to a fake returning a
summary dict; no live ECB call is ever made.

Layers exercised:
  • app._fx_due(hrs): not due before the interval, due after, interval 0 => never due.
  • app._fx_tick(): a due tick calls fetch_and_store EXACTLY once, records fx_refresh_last_run,
    and logs an import-log "fx" success; a raising fetch_and_store is SWALLOWED (tick never
    raises) and last_run is NOT advanced (so it retries); default-OFF does nothing.
  • leader election: the tick runs inside the notify-scheduler leader guard, and a second
    "concurrent" tick after the first recorded last_run does NOT fetch again.
  • web: admin set_fx_schedule persists the setting (admin-only).
"""
import datetime
import re

import pytest


@pytest.fixture(autouse=True)
def _clean_fx_settings():
    """Each test starts with the scheduler OFF and no recorded last-run (the autouse
    conftest snapshot restores whatever existed before, so this only normalises the
    in-test starting point)."""
    import auth
    auth.set_setting("fx_refresh_interval_hours", "0")
    con = auth.connect()
    con.execute("DELETE FROM app_settings WHERE key='fx_refresh_last_run'")
    con.commit(); con.close()


def _set_last_run(dt):
    import auth
    auth.set_setting("fx_refresh_last_run", dt.isoformat())


def _last_run():
    import auth
    return auth.get_setting("fx_refresh_last_run", "") or ""


# ---------------------------------------------------------------- due-logic
def test_due_interval_zero_never_due():
    import app as A
    assert A._fx_due(0) is False
    assert A._fx_due(-1) is False


def test_due_when_no_prior_run():
    import app as A
    # interval>0 and nothing ever recorded -> due immediately
    assert A._fx_due(24) is True


def test_due_not_before_interval_then_due_after():
    import app as A
    now = datetime.datetime.utcnow()

    # last run 2h ago, interval 24h -> NOT due
    _set_last_run(now - datetime.timedelta(hours=2))
    assert A._fx_due(24) is False

    # last run 25h ago -> due
    _set_last_run(now - datetime.timedelta(hours=25))
    assert A._fx_due(24) is True


def test_due_unparseable_last_run_treated_as_due():
    import app as A
    import auth
    auth.set_setting("fx_refresh_last_run", "garbage-timestamp")
    assert A._fx_due(24) is True


# ---------------------------------------------------------------- _fx_tick
def test_tick_off_does_nothing(monkeypatch):
    import app as A
    import ecb_rates as ECB
    calls = []
    monkeypatch.setattr(ECB, "fetch_and_store", lambda *a, **k: calls.append(1) or {})
    # interval 0 (default) -> tick is a no-op
    assert A._fx_tick() is False
    assert calls == []
    assert _last_run() == ""


def test_tick_due_fetches_once_and_records(monkeypatch):
    import app as A
    import ecb_rates as ECB
    import import_log as IL
    import auth
    auth.set_setting("fx_refresh_interval_hours", "24")

    calls = []
    fake = {"source": "ECB eurofxref-daily", "asof": "2026-06-19", "days": 1,
            "rows": 30, "currencies": ["PLN", "USD", "SEK"]}
    monkeypatch.setattr(ECB, "fetch_and_store", lambda *a, **k: calls.append((a, k)) or fake)
    logged = []
    monkeypatch.setattr(IL, "log", lambda *a, **k: logged.append((a, k)))

    assert A._fx_tick() is True
    assert len(calls) == 1                         # fetched exactly once
    # last-run recorded (so it won't fetch again until the interval elapses)
    assert _last_run() != ""
    # import-log "fx" success entry, actor=scheduler
    assert len(logged) == 1
    (chan, src, status), kw = logged[0]
    assert chan == "fx" and status == "success"
    assert kw.get("actor") == "scheduler"
    assert kw.get("records") == 30


def test_tick_not_due_does_not_fetch(monkeypatch):
    import app as A
    import ecb_rates as ECB
    import auth
    auth.set_setting("fx_refresh_interval_hours", "24")
    # recorded a successful run 1 minute ago -> not due
    _set_last_run(datetime.datetime.utcnow() - datetime.timedelta(minutes=1))
    calls = []
    monkeypatch.setattr(ECB, "fetch_and_store", lambda *a, **k: calls.append(1) or {})
    assert A._fx_tick() is False
    assert calls == []


def test_tick_failure_swallowed_and_last_run_not_advanced(monkeypatch):
    import app as A
    import ecb_rates as ECB
    import import_log as IL
    import auth
    auth.set_setting("fx_refresh_interval_hours", "24")
    # a prior successful run far in the past -> due now
    old = datetime.datetime(2026, 1, 1, 0, 0, 0)
    _set_last_run(old)

    def boom(*a, **k):
        raise RuntimeError("all ECB sources failed")
    monkeypatch.setattr(ECB, "fetch_and_store", boom)
    logged = []
    monkeypatch.setattr(IL, "log", lambda *a, **k: logged.append((a, k)))

    # never raises out of the tick
    assert A._fx_tick() is True
    # last_run NOT advanced -> still the old timestamp -> still DUE for retry
    assert _last_run() == old.isoformat()
    assert A._fx_due(24) is True
    # a "failed" fx import-log entry was written
    assert any(a[0] == "fx" and a[2] == "failed" for (a, _k) in logged)


def test_tick_only_fetches_once_per_due_window(monkeypatch):
    """Two back-to-back ticks (simulating two workers under the same leader window):
    the first records last_run, the second sees it as NOT due and does not fetch."""
    import app as A
    import ecb_rates as ECB
    import auth
    auth.set_setting("fx_refresh_interval_hours", "24")
    calls = []
    fake = {"source": "x", "asof": "2026-06-19", "rows": 1, "currencies": ["USD"]}
    monkeypatch.setattr(ECB, "fetch_and_store", lambda *a, **k: calls.append(1) or fake)
    A._fx_tick()
    A._fx_tick()
    assert len(calls) == 1                         # exactly one fetch per due window


# ---------------------------------------------------------------- leader election
def test_tick_runs_under_notify_scheduler_leader_guard(monkeypatch):
    """The fx sweep rides the notify-scheduler's SINGLE elected leader (process_lock
    "notify-scheduler"), it does NOT roll its own election. Drive ONE loop body and assert
    _fx_tick fires only when the leader lease is acquired."""
    import app as A
    import process_lock

    acquired = {"notify-scheduler": True}
    monkeypatch.setattr(process_lock, "acquire",
                        lambda name, **k: acquired.get(name, False))

    fx_called = []
    monkeypatch.setattr(A, "_fx_tick", lambda: fx_called.append(1))
    monkeypatch.setattr(A, "_scrape_tick", lambda: None)
    monkeypatch.setattr(A, "_notify_tick", lambda: None)

    class _StopLoop(Exception):
        pass

    monkeypatch.setattr(A.time, "sleep", lambda *_a: (_ for _ in ()).throw(_StopLoop()))
    # record_health_sample is best-effort; stub it
    import waiting_room as WR
    monkeypatch.setattr(WR, "record_health_sample", lambda: None)

    with pytest.raises(_StopLoop):
        A._notify_loop()
    assert fx_called == [1]                        # leader -> fx tick ran exactly once

    # NON-leader: lease not acquired -> the loop body (incl. _fx_tick) is skipped
    acquired["notify-scheduler"] = False
    fx_called.clear()
    with pytest.raises(_StopLoop):
        A._notify_loop()
    assert fx_called == []                          # not leader -> no fx tick


# ---------------------------------------------------------------- web (admin)
def _csrf(client, path="/fx"):
    body = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


def test_web_set_fx_schedule_persists(client):
    import auth
    tok = _csrf(client)
    r = client.post("/fx", data={"_csrf": tok, "__act": "set_fx_schedule",
                                 "fx_interval": "24"})
    assert r.status_code == 200
    assert auth.get_setting("fx_refresh_interval_hours") == "24"

    # turning it back to 0 disables it
    tok = _csrf(client)
    r = client.post("/fx", data={"_csrf": tok, "__act": "set_fx_schedule",
                                 "fx_interval": "0"})
    assert auth.get_setting("fx_refresh_interval_hours") == "0"
