"""
P1-B — notify.py builds a READ-ONLY action digest and sends it via an INJECTED
transport, so no live SMTP is touched in tests.

We seed an expiring customer document (temp customers.db) and a failed intake job
(temp intake.db), inject a fake transport, and assert send_digest:
  * calls transport.send exactly once with a subject and a body that mentions the
    expiring document kind and the failed-job count, and
  * no-ops (and logs) when no recipients are configured.
"""
import os
import datetime
import importlib


class FakeTransport:
    def __init__(self):
        self.sends = []

    def send(self, to, subject, html, text):
        self.sends.append({"to": to, "subject": subject, "html": html, "text": text})


def _seed(tmp_path, monkeypatch):
    # one already-expired POA document (read-only source patched, not the live DB)
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    import customer_master as CM
    monkeypatch.setattr(CM, "expiring_documents", lambda con, within_days=60: [
        {"customer": "ACME", "kind": "Power of Attorney", "filename": "poa.pdf",
         "country": "DE", "valid_until": yesterday, "days_left": -1}])

    # isolated intake.db with one failed job
    monkeypatch.setenv("INTAKE_DB", str(tmp_path / "intake.db"))
    monkeypatch.setenv("INTAKE_INBOX", str(tmp_path / "inbox"))
    import waiting_room as WR
    importlib.reload(WR)
    icon = WR.connect()
    icon.execute("INSERT INTO intake_jobs (sha256, filename, status) "
                 "VALUES ('cafe', 'broken.pdf', 'failed')")
    icon.commit(); icon.close()

    import notify
    importlib.reload(notify)
    return notify


def test_send_digest_uses_injected_transport(tmp_path, monkeypatch):
    notify = _seed(tmp_path, monkeypatch)

    # no claims source noise — keep the digest to the seeded signals
    import vat_refund as VR
    monkeypatch.setattr(VR, "claims_overview", lambda y: {"to_submit": [], "open": []})
    monkeypatch.setattr(VR, "recovery_report", lambda y: ([], {}))

    import auth
    monkeypatch.setattr(auth, "get_setting",
                        lambda k, d=None: "ops@example.test" if k == "notify_recipients" else d)

    fake = FakeTransport()
    sent = notify.send_digest(transport=fake)

    assert sent is True
    assert len(fake.sends) == 1
    msg = fake.sends[0]
    assert msg["subject"]                                   # has a subject
    assert msg["to"] == ["ops@example.test"]
    # the expiring document kind and the failed-job count both appear
    assert "Power of Attorney" in msg["text"]
    assert "Power of Attorney" in msg["html"]
    assert "stuck in intake" in msg["text"]
    assert "failed=1" in msg["text"]


def test_digest_surfaces_pending_document_requests(tmp_path, monkeypatch):
    """WO4: an open document request (e.g. a PoA out for signature) gets its own digest
    section — informational chase only, mirroring the expiring-docs section."""
    notify = _seed(tmp_path, monkeypatch)
    import customer_master as CM
    monkeypatch.setattr(CM, "pending_document_requests", lambda con, **k: [
        {"customer": "ACME", "kind": "power_of_attorney", "refund_country": "Belgium",
         "age_days": 21, "overdue": True}])
    import vat_refund as VR
    monkeypatch.setattr(VR, "claims_overview", lambda y: {"to_submit": [], "open": []})
    monkeypatch.setattr(VR, "recovery_report", lambda y: ([], {}))

    text, html = notify.render_digest()
    assert "Pending document requests" in text and "Pending document requests" in html
    # the kind is humanised, the country + age + overdue flag carried through
    assert "power of attorney" in text and "ACME" in text and "Belgium" in text
    assert "21d ago" in text and "OVERDUE" in text


def test_no_recipients_is_a_logged_noop(tmp_path, monkeypatch):
    notify = _seed(tmp_path, monkeypatch)
    import auth
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: d)   # nothing configured

    infos = []
    monkeypatch.setattr(notify.log, "info", lambda *a, **k: infos.append((a, k)))

    fake = FakeTransport()
    sent = notify.send_digest(transport=fake)

    assert sent is False
    assert fake.sends == []          # never attempted
    assert infos, "the no-recipient skip should be logged"


def test_html_values_are_escaped(tmp_path, monkeypatch):
    notify = _seed(tmp_path, monkeypatch)
    import vat_refund as VR
    # a blocked claim carrying an HTML-bearing entity name proves escaping
    monkeypatch.setattr(VR, "claims_overview", lambda y: {
        "to_submit": [{"entity": "<b>Evil</b>", "country": "DE", "period": "2026-Q1",
                       "ready": False, "issues": ["customer not activated"]}],
        "open": []})
    monkeypatch.setattr(VR, "recovery_report", lambda y: ([], {}))
    text, html = notify.render_digest()
    assert "<b>Evil</b>" not in html
    assert "&lt;b&gt;Evil&lt;/b&gt;" in html
