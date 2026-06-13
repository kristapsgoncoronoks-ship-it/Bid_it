"""
Upload/intake MONITORING PANEL (work order P1-E).

The monitoring panel is the existing /queue page enhanced: a status summary from
waiting_room.counts(), a per-job monitor table (waiting_room.monitor_rows() — stuck
jobs first, with the supplier/confidence the extractor resolved), and the durable
extraction-outcome trail from import_log. The "Send now" (held) / "Re-queue" (failed)
actions are the existing /queue POST __act=requeue handler (waiting_room.requeue).

These tests seed jobs in several states directly in an isolated intake.db (no live
extraction/AI/SMTP), then exercise the panel and the requeue action through the web
layer, asserting counts + escaping + CSRF/permission gating.
"""
import datetime

import pytest


# a filename crafted to break out of the table cell if it were not escaped
EVIL_NAME = '<img src=x onerror=alert(1)>pwn.pdf'


def _seed(IQ):
    """Insert jobs across the lifecycle states straight into the queue DB. Returns
    a dict of {state: job_id} for the rows we care about in assertions. Each job
    gets a real (dummy) inbox file so requeue() — which refuses if the source bytes
    are gone — can nudge it."""
    import os
    os.makedirs(IQ.INBOX, exist_ok=True)
    con = IQ.connect()
    now = IQ._now()
    rows = [
        # (filename, status, attempts, error, next_attempt_at, draft)
        (EVIL_NAME, "failed", 5, "RuntimeError: supplier parser blew up <boom>", None, None),
        ("held-batch.pdf", "held", 3,
         "held after 6 token-quota retries — press Send to retry manually", None, None),
        ("waiting-batch.pdf", "waiting", 2, "waiting for AI tokens (retry 2/6)",
         "2099-01-01 00:00:00", None),
        ("queued-batch.pdf", "queued", 0, None, None, None),
        ("ready-batch.pdf", "ready", 1, None, None,
         '{"supplier": "ACME OIL", "confidence": "high"}'),
        ("done-batch.pdf", "done", 1, None, None, None),
    ]
    ids = {}
    for i, (fn, st, att, err, nxt, draft) in enumerate(rows):
        with open(os.path.join(IQ.INBOX, f"sha{i}.bin"), "wb") as fh:
            fh.write(b"%PDF-1.4 seed")
        cur = con.execute(
            """INSERT INTO intake_jobs
               (sha256, filename, size, backend, period, uploaded_by, uploaded_at,
                status, attempts, next_attempt_at, draft, error, stored_path)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"{i:064d}", fn, 1234, "none", "2026-05", "amy", now,
             st, att, nxt, draft, err, f"sha{i}.bin"))
        ids[st] = cur.lastrowid
    con.commit()
    con.close()
    return ids


@pytest.fixture()
def monitor(monkeypatch, tmp_path):
    """Isolate the intake queue DB/inbox and the import_log DB so the panel reads
    only our seeded data and nothing leaks into the repo's runtime DBs."""
    import waiting_room as IQ
    import import_log as IL
    monkeypatch.setattr(IQ, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(IQ, "INBOX", str(tmp_path / "inbox"))
    IQ._SCHEMA_READY.clear()
    monkeypatch.setattr(IL, "DB", str(tmp_path / "import_log.db"))
    if hasattr(IL, "_SCHEMA_READY"):
        IL._SCHEMA_READY.clear()
    return IQ


def test_panel_renders_counts_states_and_escapes(client, monitor):
    ids = _seed(monitor)
    html = client.get("/queue").get_data(as_text=True)

    # status summary: the state KPIs are present
    for label in ("queued", "processing", "ready", "failed",
                  "held — needs manual send", "waiting for API tokens"):
        assert label in html, f"missing KPI label: {label}"

    # the failed job's filename appears ESCAPED, never as a live tag
    assert "<img src=x onerror=alert(1)>pwn.pdf" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;pwn.pdf" in html

    # the failed job's error reason is surfaced and escaped (the <boom> is neutralised)
    assert "supplier parser blew up" in html
    assert "<boom>" not in html
    assert "&lt;boom&gt;" in html

    # held job + its error/state hint render
    assert "held-batch.pdf" in html
    assert "press Send to retry manually" in html

    # the per-state labels show in the monitor table
    for st in ("failed", "held", "waiting", "queued", "ready"):
        assert st in html

    # extraction outcome / supplier from a ready draft is shown
    assert "ACME OIL" in html
    assert "high" in html


def test_held_job_send_now_requeues(client, monitor):
    """'Send now' on a held job = the existing __act=requeue action: it transitions
    the job back to a pending (queued) state for the worker to pick up again."""
    import re
    ids = _seed(monitor)
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/queue").get_data(as_text=True)).group(1)
    r = client.post("/queue", data={
        "_csrf": tok, "__act": "requeue", "job": str(ids["held"])})
    assert r.status_code == 200
    assert monitor.get_job(ids["held"])["status"] == "queued"


def test_failed_job_requeue(client, monitor):
    """'Re-queue' on a failed job resets it to queued and clears the attempt counter,
    without weakening the lease/retry invariants (requeue just clears the gates)."""
    import re
    ids = _seed(monitor)
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/queue").get_data(as_text=True)).group(1)
    r = client.post("/queue", data={
        "_csrf": tok, "__act": "requeue", "job": str(ids["failed"])})
    assert r.status_code == 200
    job = monitor.get_job(ids["failed"])
    assert job["status"] == "queued"
    assert job["attempts"] == 0
    assert job["error"] is None


def test_requeue_action_is_csrf_protected(client, monitor):
    """A tokenless POST to the action is rejected (400) and the job is untouched."""
    ids = _seed(monitor)
    client.get("/queue")  # establish the session CSRF secret first
    r = client.post("/queue", data={"__act": "requeue", "job": str(ids["held"])})
    assert r.status_code == 400
    assert monitor.get_job(ids["held"])["status"] == "held"


def test_panel_is_data_import_gated(monitor):
    """A processor without the data_import capability cannot reach the panel (403)."""
    import auth
    import app as A
    user = "pytest_noimport"
    # make a processor and revoke data_import
    try:
        auth.add_user(user, "Pytest!Pw123", role="processor")
    except Exception:
        pass
    auth.set_permission("processor", "data_import", False)
    try:
        c = A.app.test_client()
        r = c.post("/login", data={"username": user, "password": "Pytest!Pw123"})
        assert r.status_code == 302, f"login failed: {r.status_code}"
        r = c.get("/queue")
        assert r.status_code == 403, f"expected 403, got {r.status_code}"
    finally:
        # restore the default (granted) so we don't leak state to other tests
        auth.set_permission("processor", "data_import", True)
