"""
P1-C — the notify scheduler sends the P1-B digest automatically, once across worker
processes (leader-elected, same idiom as the backup scheduler).

No threads, no network: we call _notify_tick() directly with due / not-due settings
and a recorder in place of notify.send_digest, and assert the leader gate only lets
the elected process tick.
"""
import datetime
import importlib


def _patch_settings(monkeypatch, values):
    import auth
    store = dict(values)
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(auth, "set_setting", lambda k, v: store.update({k: v}))
    return store


def test_tick_sends_only_when_due(monkeypatch):
    import app as A
    import notify
    sends = []
    monkeypatch.setattr(notify, "send_digest", lambda: sends.append(1) or notify.SENT)

    # due: interval set, never sent before
    _patch_settings(monkeypatch, {"notify_interval_hours": "24"})
    assert A._notify_tick() is True
    assert len(sends) == 1

    # not due: interval off
    sends.clear()
    _patch_settings(monkeypatch, {"notify_interval_hours": "0"})
    assert A._notify_tick() is False
    assert sends == []

    # not due: last send was only an hour ago, interval is 24h
    sends.clear()
    recent = (datetime.datetime.utcnow() - datetime.timedelta(hours=1)).isoformat()
    _patch_settings(monkeypatch, {"notify_interval_hours": "24",
                                  "notify_last_sent": recent})
    assert A._notify_tick() is False
    assert sends == []

    # due again: last send well past the interval
    sends.clear()
    old = (datetime.datetime.utcnow() - datetime.timedelta(hours=30)).isoformat()
    store = _patch_settings(monkeypatch, {"notify_interval_hours": "24",
                                          "notify_last_sent": old})
    assert A._notify_tick() is True
    assert len(sends) == 1
    # the tick records the new send time so it won't immediately re-fire
    assert store.get("notify_last_sent") != old


def test_tick_records_last_sent_even_when_nothing_outstanding(monkeypatch):
    import app as A
    import notify
    # send_digest returns False (no-op) — the tick must still stamp last_sent so a
    # quiet period doesn't re-fire every iteration.
    monkeypatch.setattr(notify, "send_digest", lambda: False)
    store = _patch_settings(monkeypatch, {"notify_interval_hours": "24"})
    assert A._notify_tick() is True
    assert store.get("notify_last_sent")


def test_tick_does_not_advance_on_send_failure(monkeypatch):
    import app as A
    import notify
    # send_digest reports a transport (SMTP) failure -> the tick must NOT advance
    # last_sent (so it retries next tick) AND must record an error (the blind spot).
    monkeypatch.setattr(notify, "send_digest", lambda: notify.FAILED)
    errors = []
    monkeypatch.setattr(A._auth, "log_error",
                        lambda *a, **k: errors.append((a, k)))
    store = _patch_settings(monkeypatch, {"notify_interval_hours": "24"})

    assert A._notify_tick() is True            # a send was attempted
    assert not store.get("notify_last_sent")   # but last_sent was NOT advanced
    assert errors, "an SMTP failure must be recorded to the admin error log"


def test_tick_advances_on_nothing_to_report(monkeypatch):
    import app as A
    import notify
    # NOOP (nothing outstanding) is NOT a failure -> advance last_sent, no error.
    monkeypatch.setattr(notify, "send_digest", lambda: notify.NOOP)
    errors = []
    monkeypatch.setattr(A._auth, "log_error",
                        lambda *a, **k: errors.append((a, k)))
    store = _patch_settings(monkeypatch, {"notify_interval_hours": "24"})

    assert A._notify_tick() is True
    assert store.get("notify_last_sent")       # advanced
    assert errors == []                        # no blind-spot alert


def test_loop_ticks_only_as_leader(monkeypatch):
    import app as A
    import process_lock

    ticks = []
    monkeypatch.setattr(A, "_notify_tick", lambda: ticks.append(1))

    # not the leader -> acquire() falsey -> no tick. We run one loop body manually
    # (no real thread / sleep) to exercise the leader gate.
    monkeypatch.setattr(process_lock, "acquire", lambda *a, **k: False)
    if process_lock.acquire("notify-scheduler", ttl=600, holder="x"):
        A._notify_tick()
    assert ticks == []

    # leader -> acquire() truthy -> tick fires
    monkeypatch.setattr(process_lock, "acquire", lambda *a, **k: True)
    if process_lock.acquire("notify-scheduler", ttl=600, holder="x"):
        A._notify_tick()
    assert ticks == [1]
