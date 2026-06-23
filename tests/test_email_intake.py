"""Automated invoice inbound (email_intake.py): polls a mailbox and feeds attachments into the
SAME intake queue as a web upload (waiting_room.enqueue, SHA-dedup). Pluggable provider so the
poll logic is tested without a live IMAP server."""
import pytest

import email_intake as EI
import waiting_room


@pytest.fixture()
def _q(tmp_path, monkeypatch):
    monkeypatch.setattr(waiting_room, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(waiting_room, "INBOX", str(tmp_path / "inbox"))
    return waiting_room


@pytest.fixture()
def _settings(monkeypatch):
    store = {}
    import auth
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(auth, "set_setting", lambda k, v: store.__setitem__(k, v))
    return store


class _Msg:
    def __init__(self, uid, atts):
        self.uid, self.subject, self.attachments = uid, "Invoice", atts


class _Provider:
    def __init__(self, msgs):
        self.msgs, self.seen = msgs, []

    def fetch(self, n=50):
        for m in self.msgs[:n]:
            yield m

    def mark_seen(self, uid):
        self.seen.append(uid)


_PDF = b"%PDF-1.4 fake invoice"


def test_poll_enqueues_invoice_attachments_and_skips_others(_q):
    prov = _Provider([
        _Msg("1", [("inv1.pdf", _PDF), ("logo.png", b"x"), ("e.xml", b"<Invoice/>")]),
        _Msg("2", [("big.pdf", b"x" * (EI._MAX_BYTES + 1)), ("note.txt", b"hi")]),
    ])
    res = EI.poll(provider=prov)
    assert res["messages"] == 2
    assert res["enqueued"] == 2          # inv1.pdf + e.xml; png/txt/oversize skipped
    assert res["skipped"] == 3           # logo.png + oversize big.pdf + note.txt
    assert res["errors"] == 0
    assert prov.seen == ["1", "2"]       # both marked seen after enqueue
    # the attachments really landed in the queue
    assert _q.connect().execute("SELECT COUNT(*) FROM intake_jobs").fetchone()[0] == 2


def test_poll_dedups_identical_attachment(_q):
    prov = _Provider([_Msg("1", [("a.pdf", _PDF)]), _Msg("2", [("a.pdf", _PDF)])])
    res = EI.poll(provider=prov)
    assert res["enqueued"] == 1 and res["duplicates"] == 1     # same bytes -> one job
    assert _q.connect().execute("SELECT COUNT(*) FROM intake_jobs").fetchone()[0] == 1


def test_poll_disabled_and_unconfigured(_settings):
    assert "disabled" in EI.poll()["skipped"]                 # default OFF
    _settings["email_intake_enabled"] = "on"
    assert "not configured" in EI.poll()["skipped"]           # enabled but no host/user/pw


def test_poll_never_raises_on_bad_provider(_q):
    class Boom:
        def fetch(self, n=50):
            raise RuntimeError("imap down")
    res = EI.poll(provider=Boom())
    assert res["errors"] >= 1 and res["enqueued"] == 0        # logged, not raised


def test_config_password_is_sealed_and_round_trips(_settings):
    ok, _ = EI.set_config({"enabled": "on", "host": "imap.x", "user": "u@x"},
                          password="s3cret")
    assert ok
    c = EI.config()
    assert c["enabled"] and c["host"] == "imap.x" and c["has_password"] is True
    # the password is NOT stored in plaintext
    assert "s3cret" not in str(_settings)
    assert EI._password() == "s3cret"                          # unseals correctly
    # a blank password leaves it; clear removes it
    EI.set_config({"host": "imap.x"}, password=None)
    assert EI.config()["has_password"] is True
    EI.set_config({"host": "imap.x"}, clear_password=True)
    assert EI.config()["has_password"] is False
