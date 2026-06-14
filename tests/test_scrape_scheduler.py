"""Off-by-default scheduled-pull SCHEDULER for supplier-portal fetching.

The cardinal safety property under test: nothing is ever auto-pulled unless an admin has
explicitly (a) armed the global kill-switch AND (b) set a per-portal interval > 0 on an
enabled portal that has stored credentials. The default install (switch OFF, every
interval 0) is inert.

Three layers are exercised:
  • portal_scraper.set_config(interval_hours=...) persistence + round-trip, and the
    READ-ONLY, never-raising due_portal_fetches() eligibility logic (now injected).
  • app._scrape_tick(): the master gate — enqueues NOTHING with the switch OFF, one
    fetch per due portal with it ON, and never raises if its callees throw.
  • web: the admin set_scrape_scheduler / save_config(interval) actions (existing gating).

Time is injected (no real sleeps) for determinism."""
import datetime
import importlib

import pytest


@pytest.fixture()
def ps(tmp_path, monkeypatch):
    """A reloaded portal_scraper pointed at a throwaway portal.db."""
    import portal_scraper
    importlib.reload(portal_scraper)
    monkeypatch.setattr(portal_scraper, "DB", str(tmp_path / "portal.db"))
    portal_scraper._SCHEMA_READY.clear()
    return portal_scraper


def _seed_run(ps, supplier, entity, finished, status="ok"):
    """Seed a portal_runs row with an explicit `finished` timestamp (UTC string)."""
    con = ps.connect()
    con.execute("INSERT INTO portal_runs (supplier, entity, status, finished) VALUES (?,?,?,?)",
                (supplier.upper(), entity, status, finished))
    con.commit()
    con.close()


# ---------------------------------------------------------------- config round-trip
def test_interval_hours_persists_and_defaults_zero(ps):
    ps.set_config("DEMO", "demo")                       # no interval -> default 0
    assert ps.get_config("DEMO")["interval_hours"] == 0

    ps.set_config("DEMO", "demo", interval_hours=6)
    assert ps.get_config("DEMO")["interval_hours"] == 6.0
    # surfaced in list_configs and list_portals too
    assert [c["interval_hours"] for c in ps.list_configs() if c["supplier"] == "DEMO"] == [6.0]
    ps.set_credentials("DEMO", "EntA", "u", "s")
    assert any(p["supplier"] == "DEMO" and p["interval_hours"] == 6.0
               for p in ps.list_portals())

    # an unparseable interval is coerced to 0, not an error
    ps.set_config("DEMO", "demo", interval_hours="not-a-number")
    assert ps.get_config("DEMO")["interval_hours"] == 0


# ---------------------------------------------------------------- due eligibility
def test_due_requires_enabled_interval_and_creds(ps):
    # enabled + interval>0 + creds + never run -> DUE
    ps.set_config("AA", "demo", enabled=True, interval_hours=6)
    ps.set_credentials("AA", "EntA", "u", "s")
    assert ("AA", "EntA") in ps.due_portal_fetches()

    # interval 0 -> excluded
    ps.set_config("BB", "demo", enabled=True, interval_hours=0)
    ps.set_credentials("BB", "EntA", "u", "s")
    # disabled -> excluded
    ps.set_config("CC", "demo", enabled=False, interval_hours=6)
    ps.set_credentials("CC", "EntA", "u", "s")
    # interval>0, enabled, but NO creds -> excluded
    ps.set_config("DD", "demo", enabled=True, interval_hours=6)

    due = ps.due_portal_fetches()
    assert ("AA", "EntA") in due
    assert all(s not in ("BB", "CC", "DD") for (s, _e) in due)


def test_due_respects_last_run_age(ps):
    ps.set_config("AA", "demo", enabled=True, interval_hours=6)
    ps.set_credentials("AA", "EntA", "u", "s")
    now = datetime.datetime(2026, 6, 14, 12, 0, 0)

    # last successful run 2h ago, interval 6h -> NOT yet due
    _seed_run(ps, "AA", "EntA", "2026-06-14 10:00:00")
    assert ("AA", "EntA") not in ps.due_portal_fetches(now=now)

    # last successful run 8h ago -> due again
    _seed_run(ps, "AA", "EntA", "2026-06-14 04:00:00")
    assert ("AA", "EntA") in ps.due_portal_fetches(now=now)


def test_due_failed_run_does_not_count_as_recent(ps):
    """Only SUCCESSFUL runs satisfy the interval; a recent FAILED run leaves it due."""
    ps.set_config("AA", "demo", enabled=True, interval_hours=6)
    ps.set_credentials("AA", "EntA", "u", "s")
    now = datetime.datetime(2026, 6, 14, 12, 0, 0)
    _seed_run(ps, "AA", "EntA", "2026-06-14 11:30:00", status="failed")
    assert ("AA", "EntA") in ps.due_portal_fetches(now=now)


def test_due_unparseable_finished_treated_as_due(ps):
    ps.set_config("AA", "demo", enabled=True, interval_hours=6)
    ps.set_credentials("AA", "EntA", "u", "s")
    _seed_run(ps, "AA", "EntA", "garbage-timestamp")
    assert ("AA", "EntA") in ps.due_portal_fetches()


def test_due_never_raises_on_broken_state(ps, monkeypatch):
    def boom():
        raise RuntimeError("db gone")
    monkeypatch.setattr(ps, "connect", boom)
    assert ps.due_portal_fetches() == []                # never raises -> []


# ---------------------------------------------------------------- _scrape_tick gate
def test_scrape_tick_master_switch_off_enqueues_nothing(monkeypatch):
    import app as A
    import portal_scraper as PS
    import waiting_room as IQ
    monkeypatch.setattr(A, "scrape_scheduler_on", lambda: False)
    monkeypatch.setattr(PS, "due_portal_fetches",
                        lambda *a, **k: [("AA", "EntA")])   # a portal IS due

    calls = []
    monkeypatch.setattr(IQ, "enqueue_fetch", lambda *a, **k: calls.append((a, k)))
    A._scrape_tick()
    assert calls == []                                  # master gate held it inert


def test_scrape_tick_switch_on_enqueues_each_due(monkeypatch):
    import app as A
    import portal_scraper as PS
    import waiting_room as IQ
    monkeypatch.setattr(A, "scrape_scheduler_on", lambda: True)
    monkeypatch.setattr(PS, "due_portal_fetches",
                        lambda *a, **k: [("AA", "EntA"), ("BB", "EntB")])

    calls = []
    monkeypatch.setattr(IQ, "enqueue_fetch",
                        lambda supplier, entity, **k: calls.append((supplier, entity, k)))
    A._scrape_tick()
    assert [(s, e) for (s, e, _k) in calls] == [("AA", "EntA"), ("BB", "EntB")]
    assert all(k.get("user") == "scheduler" for (_s, _e, k) in calls)


def test_scrape_tick_never_raises_when_callees_throw(monkeypatch):
    import app as A
    import portal_scraper as PS
    import waiting_room as IQ
    monkeypatch.setattr(A, "scrape_scheduler_on", lambda: True)

    def boom_due(*a, **k):
        raise RuntimeError("eligibility blew up")
    monkeypatch.setattr(PS, "due_portal_fetches", boom_due)
    A._scrape_tick()                                    # swallowed, no raise

    monkeypatch.setattr(PS, "due_portal_fetches", lambda *a, **k: [("AA", "EntA")])

    def boom_enq(*a, **k):
        raise RuntimeError("enqueue blew up")
    monkeypatch.setattr(IQ, "enqueue_fetch", boom_enq)
    A._scrape_tick()                                    # per-portal failure swallowed


# ---------------------------------------------------------------- web (admin gating)
def _csrf(client, path="/pricing"):
    import re
    body = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


def test_web_set_scrape_scheduler_flips_setting(client):
    import auth
    tok = _csrf(client)
    r = client.post("/pricing/portal", data={
        "_csrf": tok, "__act": "set_scrape_scheduler", "on": "1"})
    assert r.status_code == 200
    assert auth.get_setting("scrape_scheduler_enabled") == "1"
    assert b"now ON" in r.data or b"ON" in r.data

    # unchecking turns it back OFF (the master gate restored)
    tok = _csrf(client)
    r = client.post("/pricing/portal", data={
        "_csrf": tok, "__act": "set_scrape_scheduler"})
    assert auth.get_setting("scrape_scheduler_enabled") == "0"


def test_web_save_config_persists_interval(client, monkeypatch):
    import portal_scraper as PS
    seen = {}
    real = PS.set_config

    def spy(supplier, kind, **k):
        seen.update(supplier=supplier, kind=kind, **k)
        return real(supplier, kind, **k)
    monkeypatch.setattr(PS, "set_config", spy)

    tok = _csrf(client)
    r = client.post("/pricing/portal", data={
        "_csrf": tok, "__act": "save_config", "supplier": "ZZ", "kind": "demo",
        "config": "{}", "enabled": "on", "interval_hours": "12"})
    assert r.status_code == 200
    assert seen.get("interval_hours") == 12.0
