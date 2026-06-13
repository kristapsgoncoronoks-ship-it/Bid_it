"""
P1-D — silent stalls become visible in the dashboard worklist:
  * claims blocked on an UNMATCHED/unresolved invoice ref get a "Resolve UNMATCHED"
    item linking to /readiness, and
  * documents stuck in the intake queue (failed/held) get a "stuck in intake" item
    linking to /queue.

The worklist is READ-ONLY (it never mutates a claim/figure) — we only assert the
rendered strings and that interpolated values are escaped.
"""
import os
import tempfile
import importlib


def test_worklist_surfaces_unmatched_and_stuck(monkeypatch):
    import app as A
    import vat_refund as VR

    # a claim blocked specifically on an unresolved invoice ref (the wording
    # vat_refund.submission_readiness emits), plus a benign HTML-bearing value to
    # prove escaping.
    monkeypatch.setattr(VR, "claims_overview", lambda y: {
        "to_submit": [
            {"entity": "<b>Acme</b>", "country": "DE", "period": "2026-Q1",
             "vat_eur": 800, "ready": False,
             "issues": ["3 unresolved invoice ref(s)"]},
        ], "open": []})
    monkeypatch.setattr(VR, "recovery_report", lambda y: ([], {}))

    # a real intake job sitting in 'failed' — drive counts() off a temp queue DB so
    # nothing touches the live intake.db.
    d = tempfile.mkdtemp()
    monkeypatch.setenv("INTAKE_DB", os.path.join(d, "intake.db"))
    monkeypatch.setenv("INTAKE_INBOX", os.path.join(d, "inbox"))
    import waiting_room as WR
    importlib.reload(WR)
    con = WR.connect()
    con.execute("INSERT INTO intake_jobs (sha256, filename, status) "
                "VALUES ('deadbeef', 'broken.pdf', 'failed')")
    con.commit(); con.close()

    html = A._worklist_card(2026)

    # (1) the unresolved-ref surfacing, with the count carried through
    assert "Resolve UNMATCHED — 3 unresolved invoice ref(s)" in html
    # (2) the stuck-intake surfacing
    assert "1 document(s) stuck in intake (failed/held) — review" in html
    # both link to their action page
    assert "/readiness" in html
    assert "/queue" in html
    # DB values are escaped, never raw
    assert "<b>Acme</b>" not in html
    assert "&lt;b&gt;Acme&lt;/b&gt;" in html

    importlib.reload(WR)   # restore module to its default DB for other tests


def test_worklist_no_surfacing_when_clean(monkeypatch):
    import app as A
    import vat_refund as VR
    monkeypatch.setattr(VR, "claims_overview", lambda y: {"to_submit": [], "open": []})
    monkeypatch.setattr(VR, "recovery_report", lambda y: ([], {}))
    import waiting_room as WR
    monkeypatch.setattr(WR, "counts", lambda: {"failed": 0, "held": 0})
    html = A._worklist_card(2026)
    assert "Resolve UNMATCHED" not in html
    assert "stuck in intake" not in html
