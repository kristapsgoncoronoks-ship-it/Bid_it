"""
Decoupling D5's WEB SURFACE — the one-click monthly close runs OFF the web request.

The /close admin button does NO close work itself: it ENQUEUES a fileless job
(kind='close') onto the durable intake queue and returns. The engine worker dispatches
on kind='close' and calls engine_close.close(period) — so the request never runs the
close inline nor holds a writable engine-owned product-DB handle. The requesting user is
propagated as the audit actor (as in the register path), and engine_close's own
'close-run' process_lock serialises actual execution (a contending second run re-queues).

These tests run the worker body IN-PROCESS (no live worker thread) against temp DBs, and
exercise the web surface with the shared admin/login fixtures.
"""
import importlib

import pytest


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """Point the intake queue + import log at throwaway temp files and reset the
    per-process schema caches so the temp DBs get a fresh schema."""
    import waiting_room
    importlib.reload(waiting_room)
    monkeypatch.setattr(waiting_room, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(waiting_room, "INBOX", str(tmp_path / "inbox"))
    waiting_room._SCHEMA_READY.clear()

    import import_log
    monkeypatch.setattr(import_log, "DB", str(tmp_path / "import_log.db"))
    import_log._READY.clear()
    return waiting_room


# ---------------------------------------------------------------------------
# (1) enqueue_close inserts a fileless KIND_CLOSE job; re-enqueue is idempotent
# ---------------------------------------------------------------------------
def test_enqueue_close_inserts_fileless_job(engine):
    jid, st = engine.enqueue_close("2026-05", user="alice")
    assert st == "queued"
    job = engine.get_job(jid)
    assert job["kind"] == engine.KIND_CLOSE
    assert job["period"] == "2026-05"
    assert job["uploaded_by"] == "alice"
    assert not job["stored_path"], "a close job carries no inbox bytes"


def test_enqueue_close_is_idempotent_per_period(engine):
    jid1, _ = engine.enqueue_close("2026-05", user="alice")
    jid2, st2 = engine.enqueue_close("2026-05", user="bob")
    assert jid2 == jid1, "same period must RE-QUEUE the same row, not duplicate"
    assert st2 == "queued"
    closes = [j for j in engine.jobs() if j.get("kind") == engine.KIND_CLOSE]
    assert len(closes) == 1
    assert closes[0]["uploaded_by"] == "bob", "re-confirm refreshes the actor"


# ---------------------------------------------------------------------------
# (2) the worker dispatches a queued close to _do_close (engine_close.close)
# ---------------------------------------------------------------------------
def test_worker_dispatches_close_to_engine(engine, monkeypatch):
    import engine_close
    seen = {}

    def _stub(period=None, actor="system"):
        seen["period"] = period
        seen["actor"] = actor
        return {"rows": 1, "backup": "ok"}

    monkeypatch.setattr(engine_close, "close", _stub)

    jid, _ = engine.enqueue_close("2026-05", user="alice")
    assert engine.process_one() == (jid, "done")
    assert engine.process_one() is None              # queue idle

    assert seen == {"period": "2026-05", "actor": "alice"}
    assert engine.get_job(jid)["status"] == "done"

    # a 'close'/'success' import_log event was written for the panel to read
    import import_log
    evs = import_log.recent(channel="close", status="success")
    assert any(e.get("period") == "2026-05" for e in evs), evs


# ---------------------------------------------------------------------------
# (3) lock contention RE-QUEUES for retry (not 'failed'/DLQ)
# ---------------------------------------------------------------------------
def test_lock_contention_requeues_for_retry(engine, monkeypatch):
    import engine_close

    def _contended(period=None, actor="system"):
        raise RuntimeError("another close is already running (lock 'close-run' held)")

    monkeypatch.setattr(engine_close, "close", _contended)

    jid, _ = engine.enqueue_close("2026-05", user="alice")
    assert engine.process_one() == (jid, "retry"), "contention must retry, not DLQ"
    job = engine.get_job(jid)
    assert job["status"] == "queued", f"expected re-queued, got {job['status']}"
    assert job["status"] != "failed"
    assert "already running" in (job["error"] or "")


# ---------------------------------------------------------------------------
# (4) a close job with NULL stored_path is processed (not failed for a missing file)
# ---------------------------------------------------------------------------
def test_no_file_guard_does_not_fail_close(engine, monkeypatch):
    import engine_close
    monkeypatch.setattr(engine_close, "close", lambda period=None, actor="system": {})

    jid, _ = engine.enqueue_close("2026-05", user="alice")
    assert engine.get_job(jid)["stored_path"] in (None, ""), "fileless precondition"
    # the dispatch runs the close branch BEFORE any read_bytes/file-exists check
    assert engine.process_one() == (jid, "done")
    assert engine.get_job(jid)["status"] == "done"


# ---------------------------------------------------------------------------
# (5) the /close web surface: admin GET 200 + stages, admin POST enqueues only,
#     non-admin blocked, and engine_close.close is NEVER called in-request.
# ---------------------------------------------------------------------------
def _csrf(client, path="/close"):
    import re
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_close_page_get_lists_stages_for_admin(client):
    body = client.get("/close").get_data(as_text=True)
    assert "Monthly close" in body
    for stage in ("consolidate", "build_master", "history", "invoice_control", "backup"):
        assert stage in body, f"stage {stage} missing from progress panel"


def test_close_post_enqueues_only_and_never_runs_inline(client, engine, monkeypatch):
    # engine_close.close MUST NOT be invoked during the web request — make it fail
    # the test if it is.
    import engine_close

    def _boom(*a, **k):
        raise AssertionError("engine_close.close was called in-request (must be deferred)")

    monkeypatch.setattr(engine_close, "close", _boom)

    r = client.post("/close", data={"_csrf": _csrf(client), "period": "2026-05"})
    assert r.status_code == 200
    assert "queued for" in r.get_data(as_text=True)

    closes = [j for j in engine.jobs() if j.get("kind") == engine.KIND_CLOSE]
    assert len(closes) == 1 and closes[0]["status"] == "queued"
    assert closes[0]["period"] == "2026-05"


def test_close_is_admin_only(admin_session):
    import app as A, auth
    auth.add_user("closeproc", "Pw!23456", role="processor")
    cp = A.app.test_client()
    cp.post("/login", data={"username": "closeproc", "password": "Pw!23456"})
    assert cp.get("/close").status_code == 403
    # a POST is blocked too (CSRF runs first for a tokenless POST, so assert it never 200s)
    assert cp.post("/close", data={"period": "2026-05"}).status_code in (400, 403)
    assert "Monthly close" not in cp.get("/").get_data(as_text=True)
