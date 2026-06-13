"""Tests for the durable intake 'waiting room' queue: enqueue durability, dedupe,
claim/process to ready, retry-with-backoff then failed, stale-lease reclaim, and
the done/discard lifecycle."""
import importlib
import os

import pytest


@pytest.fixture()
def iq(tmp_path, monkeypatch):
    import waiting_room
    importlib.reload(waiting_room)
    monkeypatch.setattr(waiting_room, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(waiting_room, "INBOX", str(tmp_path / "inbox"))
    waiting_room._SCHEMA_READY.clear()
    return waiting_room


def _stub_extract(monkeypatch, fn):
    import extract
    monkeypatch.setattr(extract, "extract", fn)


def test_enqueue_is_durable_and_dedupes(iq):
    jid, st = iq.enqueue(b"%PDF-1.4 one", "a.pdf", backend="none", period="2026-05", user="amy")
    assert st == "queued"
    # the bytes hit disk before the row was committed
    job = iq.get_job(jid)
    assert os.path.exists(os.path.join(iq.INBOX, job["stored_path"]))
    assert iq.read_bytes(job["stored_path"]) == b"%PDF-1.4 one"
    assert job["uploaded_by"] == "amy"
    # identical re-upload returns the SAME job (idempotent), not a duplicate
    jid2, _ = iq.enqueue(b"%PDF-1.4 one", "a.pdf")
    assert jid2 == jid
    assert iq.counts()["queued"] == 1


def test_process_to_ready(iq, monkeypatch):
    _stub_extract(monkeypatch, lambda data, name, backend=None, strict=False: {
        "supplier": "ACME", "lines": [{"invoice_no": "I1", "net": 10, "vat": 2}],
        "backend": backend or "stub", "_pdf_bytes": [("a.pdf", data)]})
    jid, _ = iq.enqueue(b"%PDF-1.4 x", "a.pdf", backend="none")
    assert iq.process_one() == (jid, "ready")
    assert iq.process_one() is None              # queue now idle
    job = iq.get_job(jid)
    assert job["status"] == "ready" and job["draft"]
    # the stored draft has no binary, but get_draft re-derives the pdf bytes
    draft, pdfs = iq.get_draft(jid)
    assert draft["supplier"] == "ACME"
    assert pdfs and pdfs[0] == b"%PDF-1.4 x"
    assert "_pdf_bytes" not in draft


def test_retry_then_fail(iq, monkeypatch):
    def boom(data, name, backend=None, strict=False):
        raise RuntimeError("backend down")
    _stub_extract(monkeypatch, boom)
    monkeypatch.setattr(iq, "MAX_ATTEMPTS", 3)
    # zero the backoff so each retry is immediately eligible for the next claim
    monkeypatch.setattr(iq, "BACKOFF_BASE", 0)
    monkeypatch.setattr(iq, "BACKOFF_MAX", 0)
    jid, _ = iq.enqueue(b"%PDF-1.4 y", "b.pdf")
    assert iq.process_one() == (jid, "retry")    # attempt 1
    job = iq.get_job(jid)
    assert job["status"] == "queued" and job["next_attempt_at"] and "backend down" in job["error"]
    assert iq.process_one() == (jid, "retry")    # attempt 2
    assert iq.process_one() == (jid, "failed")   # attempt 3 hits the cap
    assert iq.get_job(jid)["status"] == "failed"
    # the source bytes are still on disk (failed jobs are kept for inspection)
    assert os.path.exists(os.path.join(iq.INBOX, iq.get_job(jid)["stored_path"]))


def test_stale_lease_is_reclaimed(iq, monkeypatch):
    # a crash mid-processing leaves a 'processing' row with an expired lease; the
    # next claim must reclaim and reprocess it (at-least-once).
    _stub_extract(monkeypatch, lambda data, name, backend=None, strict=False: {
        "lines": [], "backend": "stub", "_pdf_bytes": []})
    jid, _ = iq.enqueue(b"%PDF-1.4 z", "c.pdf")
    con = iq.connect()
    con.execute("UPDATE intake_jobs SET status='processing', lease_until='2000-01-01 00:00:00' WHERE id=?",
                (jid,))
    con.commit(); con.close()
    assert iq.counts()["processing"] == 1
    assert iq.process_one() == (jid, "ready")    # reclaimed despite being 'processing'


def test_startup_orphan_sweep_reclaims_expired_lease(iq):
    """R2: a crashed worker leaves a 'processing' row with an expired lease. The
    startup orphan-sweep resets it to 'queued' immediately (don't wait LEASE_SECONDS
    for the in-claim reclaim). A fresh, non-expired lease is left untouched."""
    import datetime
    jid_a, _ = iq.enqueue(b"%PDF-1.4 a", "orphan.pdf")     # expired lease -> reclaim
    jid_b, _ = iq.enqueue(b"%PDF-1.4 b", "fresh.pdf")      # fresh lease -> untouched
    future = (datetime.datetime.utcnow() + datetime.timedelta(seconds=300)
              ).strftime("%Y-%m-%d %H:%M:%S")
    con = iq.connect()
    con.execute("UPDATE intake_jobs SET status='processing', lease_until='2000-01-01 00:00:00' WHERE id=?",
                (jid_a,))
    con.execute("UPDATE intake_jobs SET status='processing', lease_until=? WHERE id=?",
                (future, jid_b))
    con.commit(); con.close()

    assert iq.reclaim_orphans() == 1                       # only the expired one

    assert iq.get_job(jid_a)["status"] == "queued"         # reclaimed
    assert iq.get_job(jid_a)["lease_until"] is None
    assert iq.get_job(jid_b)["status"] == "processing"     # fresh lease untouched

    # idempotent: a second sweep reclaims nothing
    assert iq.reclaim_orphans() == 0


def test_complete_and_discard(iq, monkeypatch):
    _stub_extract(monkeypatch, lambda data, name, backend=None, strict=False: {"lines": [], "_pdf_bytes": []})
    jid, _ = iq.enqueue(b"%PDF-1.4 q", "d.pdf")
    iq.process_one()
    path = os.path.join(iq.INBOX, iq.get_job(jid)["stored_path"])
    assert iq.complete(jid) is True
    assert iq.get_job(jid)["status"] == "done"
    assert not os.path.exists(path)              # inbox bytes freed after done

    jid2, _ = iq.enqueue(b"%PDF-1.4 r", "e.pdf")
    p2 = os.path.join(iq.INBOX, iq.get_job(jid2)["stored_path"])
    assert iq.discard(jid2) is True
    assert iq.get_job(jid2) is None and not os.path.exists(p2)


def test_token_quota_waits_then_holds(iq, monkeypatch):
    """An out-of-tokens error parks the job as 'waiting' and retries on the long
    cadence without counting toward MAX_ATTEMPTS; after MAX_TOKEN_RETRIES it stops
    auto-processing and is 'held' for a manual send."""
    import extract as EX
    def out_of_tokens(data, name, backend=None, strict=False):
        if strict:
            raise EX.TransientExtractionError("openai: 429 insufficient_quota")
        return {"lines": []}
    _stub_extract(monkeypatch, out_of_tokens)
    monkeypatch.setattr(iq, "MAX_TOKEN_RETRIES", 3)
    monkeypatch.setattr(iq, "RETRY_AFTER_TOKENS", 0)   # immediate re-eligibility
    jid, _ = iq.enqueue(b"%PDF-1.4 q", "x.pdf", backend="openai")

    assert iq.process_one() == (jid, "waiting")
    j = iq.get_job(jid)
    assert j["status"] == "waiting" and j["defer_count"] == 1 and j["attempts"] == 0
    assert iq.process_one() == (jid, "waiting")        # defer 2 (still not a hard fail)
    assert iq.process_one() == (jid, "held")           # defer 3 -> held
    j = iq.get_job(jid)
    assert j["status"] == "held" and j["attempts"] == 0
    assert iq.process_one() is None                    # held jobs are NOT auto-claimed
    # the document is safe and the bytes are kept for a manual resend
    assert os.path.exists(os.path.join(iq.INBOX, j["stored_path"]))

    # the manual "Send now" button -> requeue resets it for immediate reprocessing
    assert iq.requeue(jid) is True
    j = iq.get_job(jid)
    assert j["status"] == "queued" and j["defer_count"] == 0


def test_requeue_recovers_held_to_ready_when_tokens_return(iq, monkeypatch):
    import extract as EX
    state = {"broke": True}
    def maybe(data, name, backend=None, strict=False):
        if state["broke"] and strict:
            raise EX.TransientExtractionError("claude: overloaded_error")
        return {"lines": [], "_pdf_bytes": []}
    _stub_extract(monkeypatch, maybe)
    monkeypatch.setattr(iq, "MAX_TOKEN_RETRIES", 1)
    jid, _ = iq.enqueue(b"%PDF-1.4 z", "y.pdf", backend="claude")
    assert iq.process_one() == (jid, "held")           # immediately held (cap=1)
    state["broke"] = False                              # tokens topped up
    iq.requeue(jid)                                     # user presses Send now
    assert iq.process_one() == (jid, "ready")


def test_pending_count_and_requeue_all(iq, monkeypatch):
    import extract as EX
    # one will succeed, two will get stuck (held) on token quota
    state = {"broke": True}
    def maybe(data, name, backend=None, strict=False):
        if state["broke"] and strict:
            raise EX.TransientExtractionError("openai: insufficient_quota")
        return {"lines": [], "_pdf_bytes": []}
    _stub_extract(monkeypatch, maybe)
    monkeypatch.setattr(iq, "MAX_TOKEN_RETRIES", 1)     # straight to held
    a, _ = iq.enqueue(b"%PDF-1.4 a", "a.pdf")
    b, _ = iq.enqueue(b"%PDF-1.4 b", "b.pdf")
    iq.drain()
    # both are held (unprocessed) -> they count as pending backlog
    assert iq.counts()["held"] == 2
    assert iq.pending_count() == 2

    # tokens come back; bulk "send / restart all" resets them and reprocesses
    state["broke"] = False
    reset = iq.requeue_all(("waiting", "held", "failed"))
    assert reset == 2
    assert iq.drain() == 2
    assert iq.counts()["ready"] == 2
    assert iq.pending_count() == 0                       # nothing pending now


def test_drain_processes_backlog(iq, monkeypatch):
    _stub_extract(monkeypatch, lambda data, name, backend=None, strict=False: {"lines": [], "_pdf_bytes": []})
    for i in range(5):
        iq.enqueue(f"%PDF-1.4 file{i}".encode(), f"f{i}.pdf")
    assert iq.drain(limit=10) == 5
    assert iq.counts()["ready"] == 5
    assert iq.drain() == 0                        # nothing left
