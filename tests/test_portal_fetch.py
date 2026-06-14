"""Tests for the supplier-portal FETCH decoupling (flagship slice 3): the web request
ENQUEUES a fileless KIND_FETCH job and the WORKER calls portal_scraper.scrape OFF the
request, under the per-supplier rate-limiter / circuit-breaker.

The cardinal property under test is "never inline in a web request": POST /pricing/portal
with __act=scrape must enqueue a job and must NOT call portal_scraper.scrape in-request.
Time is controlled via monkeypatch (no real sleeps) for determinism."""
import importlib
import json

import pytest


@pytest.fixture()
def iq(tmp_path, monkeypatch):
    """A reloaded waiting_room pointed at a throwaway intake.db + a throwaway
    import_log.db (so _import_log writes are observable and isolated)."""
    import waiting_room
    importlib.reload(waiting_room)
    monkeypatch.setattr(waiting_room, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(waiting_room, "INBOX", str(tmp_path / "inbox"))
    waiting_room._SCHEMA_READY.clear()
    import import_log
    importlib.reload(import_log)
    monkeypatch.setattr(import_log, "DB", str(tmp_path / "import_log.db"))
    import_log._READY.clear()
    return waiting_room


# ---------------------------------------------------------------- enqueue
def test_enqueue_fetch_is_fileless_and_idempotent(iq):
    jid, st = iq.enqueue_fetch("demo", "EntA", "2026-01-01", "2026-01-31", user="amy")
    assert st == "queued"
    job = iq.get_job(jid)
    # the supplier governs the limiter -> backend is the UPPERCASED supplier
    assert job["backend"] == "DEMO"
    assert job["kind"] == iq.KIND_FETCH
    # fileless: no inbox bytes, no period, payload round-trips
    assert job["stored_path"] is None
    assert job["period"] is None
    assert job["filename"] == "fetch demo/EntA"
    assert job["uploaded_by"] == "amy"
    p = json.loads(job["payload"])
    assert p == {"supplier": "demo", "entity": "EntA",
                 "date_from": "2026-01-01", "date_to": "2026-01-31"}

    # a re-run of the SAME (supplier, entity, window) re-queues the same row, not a dup
    jid2, st2 = iq.enqueue_fetch("demo", "EntA", "2026-01-01", "2026-01-31", user="bob")
    assert jid2 == jid and st2 == "queued"
    assert iq.counts()["queued"] == 1
    assert iq.get_job(jid)["uploaded_by"] == "bob"   # refreshed

    # a different window is a DISTINCT job
    jid3, _ = iq.enqueue_fetch("demo", "EntA", None, None)
    assert jid3 != jid


def test_enqueue_fetch_requires_supplier(iq):
    with pytest.raises(ValueError):
        iq.enqueue_fetch("", "EntA")


# ---------------------------------------------------------------- success path
def test_do_fetch_success(iq, monkeypatch):
    """A queued fetch dispatches through process_one -> _do_fetch -> scrape; the row
    ends 'done', a 'fetch'/'success' import_log event is written, and the breaker shows
    no failures for the supplier."""
    import portal_scraper, import_log
    iq.set_supplier_limit("DEMO", max_concurrent=5, min_interval_s=0)
    seen = {}

    def stub_scrape(supplier, entity, date_from=None, date_to=None):
        seen.update(supplier=supplier, entity=entity,
                    date_from=date_from, date_to=date_to)
        return {"supplier": supplier, "entity": entity, "fetched": 3, "loaded": 2}
    monkeypatch.setattr(portal_scraper, "scrape", stub_scrape)

    jid, _ = iq.enqueue_fetch("demo", "EntA", "2026-01-01", "2026-01-31")
    assert iq.process_one() == (jid, "done")
    assert seen == {"supplier": "demo", "entity": "EntA",
                    "date_from": "2026-01-01", "date_to": "2026-01-31"}

    job = iq.get_job(jid)
    assert job["status"] == "done" and job["finished_at"]

    ev = import_log.recent(channel="fetch")
    assert ev and ev[0]["status"] == "success"
    assert ev[0]["records"] == 2
    assert "2 loaded / 3 fetched" in ev[0]["message"]

    # outcome recorded ok -> breaker closed, no failure streak
    bs = iq.breaker_state("DEMO")
    assert bs == {"open": False, "open_until": None, "consec_failures": 0}

    assert iq.process_one() is None              # queue now idle


# ---------------------------------------------------------------- failure path
def test_do_fetch_failure_retries_and_drives_breaker(iq, monkeypatch):
    """A scrape that raises is re-queued for retry (not lost) and increments the
    governed supplier's consecutive-failure count via record_outcome."""
    import portal_scraper
    iq.set_supplier_limit("DEMO", max_concurrent=5, min_interval_s=0,
                          breaker_threshold=5)
    # zero the backoff so the retried row is immediately re-eligible / inspectable
    monkeypatch.setattr(iq, "BACKOFF_BASE", 0)
    monkeypatch.setattr(iq, "BACKOFF_MAX", 0)

    def boom(supplier, entity, date_from=None, date_to=None):
        raise RuntimeError("portal login refused")
    monkeypatch.setattr(portal_scraper, "scrape", boom)

    jid, _ = iq.enqueue_fetch("demo", "EntA")
    assert iq.process_one() == (jid, "retry")    # re-queued, not dead-lettered
    job = iq.get_job(jid)
    assert job["status"] == "queued" and "portal login refused" in job["error"]

    # the failure drove the breaker for the governed supplier
    assert iq.breaker_state("DEMO")["consec_failures"] == 1
    assert iq.breaker_state("DEMO")["open"] is False


# ---------------------------------------------------------------- rate-limit
def test_fetch_backend_is_rate_limited(iq):
    """The fetch job's `backend` (= UPPERCASED supplier) makes it governed: with
    max_concurrent=1, only one of two queued fetches is claimable while the first is
    in-flight (re-uses the slice-1 concurrency cap)."""
    iq.set_supplier_limit("DEMO", max_concurrent=1, min_interval_s=0)
    f1, _ = iq.enqueue_fetch("demo", "EntA")
    f2, _ = iq.enqueue_fetch("demo", "EntB")

    con = iq.connect()
    r1 = iq._claim(con); con.close()
    assert r1["id"] == f1
    assert iq.get_job(f1)["status"] == "processing"

    # DEMO is at its cap (1 in-flight) -> the 2nd fetch is not claimable
    con = iq.connect()
    r2 = iq._claim(con); con.close()
    assert r2 is None

    # complete the first -> the second becomes claimable
    iq.complete(f1)
    con = iq.connect()
    r3 = iq._claim(con); con.close()
    assert r3["id"] == f2


# ---------------------------------------------------------------- web (off-request)
def test_web_scrape_enqueues_and_does_not_call_scrape(client, monkeypatch):
    """POST /pricing/portal __act=scrape must ENQUEUE a KIND_FETCH job and must NOT
    call portal_scraper.scrape in-request (the whole point of the decoupling)."""
    import portal_scraper
    import waiting_room as IQ

    def fail_if_called(*a, **k):
        raise AssertionError("portal_scraper.scrape MUST NOT run in the web request")
    monkeypatch.setattr(portal_scraper, "scrape", fail_if_called)

    enq = {}
    real_enqueue = IQ.enqueue_fetch

    def spy(supplier, entity, date_from=None, date_to=None, user="system"):
        enq.update(supplier=supplier, entity=entity,
                   date_from=date_from, date_to=date_to, user=user)
        return real_enqueue(supplier, entity, date_from, date_to, user)
    monkeypatch.setattr(IQ, "enqueue_fetch", spy)

    # seed + carry the CSRF token (the page renders the portal-scrape form)
    import re
    body = client.get("/pricing").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', body).group(1)

    r = client.post("/pricing/portal", data={
        "_csrf": tok, "__act": "scrape", "supplier": "demo", "entity": "EntA"})
    assert r.status_code == 200
    assert b"Fetch queued" in r.data
    assert enq.get("supplier") == "demo" and enq.get("entity") == "EntA"
