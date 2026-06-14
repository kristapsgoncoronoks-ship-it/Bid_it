"""
Per-event critical alerts + the SMTP relay config UI.

The digest mailer (test_notify.py) is BATCHED on a cadence; these tests cover the
IMMEDIATE side:
  * notify.send_alert / send_test — one-shot sends via an INJECTED transport (no live
    SMTP), with the SENT/NOOP/FAILED tri-state contract;
  * notify.critical_events — the READ-ONLY snapshot of "wake someone up" conditions
    (DLQ size / age-breach / overdue filing deadline), each source independently guarded;
  * notify.critical_alert — the per-EVENT engine: fingerprint-deduped so it FIRES on a
    new critical set, NOOPs on an unchanged set, RE-FIRES after the set clears + re-breaches,
    and returns FAILED (without advancing the fingerprint) when the transport fails;
  * the admin SMTP card: __act=set_smtp persists settings WITHOUT wiping the write-only
    password on a blank field, __act=send_test_email reports the tri-state, non-admin 403.
"""
import re

import pytest


class FakeTransport:
    def __init__(self):
        self.sends = []

    def send(self, to, subject, html, text):
        self.sends.append({"to": to, "subject": subject, "html": html, "text": text})


class RaisingTransport:
    def send(self, to, subject, html, text):
        raise RuntimeError("smtp boom")


def _recipients(monkeypatch, value="ops@example.test"):
    """Point notify._recipients at a fixed recipient list (or none) without DB."""
    import auth
    monkeypatch.setattr(auth, "get_setting",
                        lambda k, d=None: value if k == "notify_recipients" else d)


# ---------------------------------------------------------------- send_alert / send_test
def test_send_alert_uses_injected_transport(monkeypatch):
    import notify
    _recipients(monkeypatch)
    fake = FakeTransport()
    res = notify.send_alert("Heads up", ["line one", "line two"], transport=fake)
    assert res is notify.SENT
    assert len(fake.sends) == 1
    msg = fake.sends[0]
    assert msg["to"] == ["ops@example.test"]
    assert msg["subject"] == "Heads up"
    assert "line one" in msg["text"] and "line two" in msg["text"]
    assert "line one" in msg["html"] and "line two" in msg["html"]


def test_send_alert_escapes_html(monkeypatch):
    import notify
    _recipients(monkeypatch)
    fake = FakeTransport()
    notify.send_alert("<b>boom</b>", ["<script>x</script>"], transport=fake)
    html = fake.sends[0]["html"]
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_send_alert_noop_no_lines(monkeypatch):
    import notify
    _recipients(monkeypatch)
    fake = FakeTransport()
    assert notify.send_alert("subj", [], transport=fake) is notify.NOOP
    assert fake.sends == []


def test_send_alert_noop_no_recipients(monkeypatch):
    import notify
    _recipients(monkeypatch, value=None)
    fake = FakeTransport()
    assert notify.send_alert("subj", ["x"], transport=fake) is notify.NOOP
    assert fake.sends == []


def test_send_alert_noop_no_transport(monkeypatch):
    import notify
    _recipients(monkeypatch)
    # no explicit transport AND no smtp_host configured -> _settings_transport() is None
    assert notify.send_alert("subj", ["x"], transport=None) is notify.NOOP


def test_send_alert_failed_not_raised(monkeypatch):
    import notify
    _recipients(monkeypatch)
    res = notify.send_alert("subj", ["x"], transport=RaisingTransport())
    assert res is notify.FAILED          # transport error mapped to FAILED, NOT raised


def test_send_test_routes_to_transport(monkeypatch):
    import notify
    _recipients(monkeypatch)
    fake = FakeTransport()
    assert notify.send_test(transport=fake) is notify.SENT
    assert "test of the SMTP relay configuration" in fake.sends[0]["text"]


# ---------------------------------------------------------------- critical_events
def test_critical_events_dlq_and_age_breach(monkeypatch):
    import notify
    import waiting_room as WR
    monkeypatch.setattr(WR, "queue_health",
                        lambda *a, **k: {"dlq": 3, "age_breach": True})
    import vat_refund as VR
    monkeypatch.setattr(VR, "approaching_deadlines", lambda **k: [])
    lines = [ln for _sev, ln in notify.critical_events()]
    assert any("3 document(s) in the dead-letter queue" in ln for ln in lines)
    assert any("oldest pending document exceeds the SLO" in ln for ln in lines)


def test_critical_events_empty_dlq(monkeypatch):
    import notify
    import waiting_room as WR
    monkeypatch.setattr(WR, "queue_health",
                        lambda *a, **k: {"dlq": 0, "age_breach": False})
    import vat_refund as VR
    monkeypatch.setattr(VR, "approaching_deadlines", lambda **k: [])
    assert notify.critical_events() == []


def test_critical_events_overdue_filing(monkeypatch):
    import notify
    import waiting_room as WR
    monkeypatch.setattr(WR, "queue_health",
                        lambda *a, **k: {"dlq": 0, "age_breach": False})
    import vat_refund as VR
    monkeypatch.setattr(VR, "approaching_deadlines", lambda **k: [
        {"kind": "filing", "overdue": True, "entity": "ACME", "country": "DE",
         "period": "2024", "deadline": "2025-09-30"},
        {"kind": "filing", "overdue": False, "entity": "OTHER", "country": "FR",
         "period": "2025", "deadline": "2026-09-30"},     # not overdue -> excluded
        {"kind": "action", "overdue": True, "entity": "X", "country": "BE",
         "period": "2024", "deadline": "2025-01-01"}])     # not a filing -> excluded
    lines = [ln for _sev, ln in notify.critical_events()]
    assert lines == ["OVERDUE: FILE ACME · DE 2024 — deadline 2025-09-30"]


def test_critical_events_swallows_source_errors(monkeypatch):
    import notify
    import waiting_room as WR
    import vat_refund as VR

    def boom(*a, **k):
        raise RuntimeError("source down")

    monkeypatch.setattr(WR, "queue_health", boom)
    monkeypatch.setattr(VR, "approaching_deadlines", boom)
    assert notify.critical_events() == []     # both sources fail -> clean empty list


# ---------------------------------------------------------------- critical_alert engine
@pytest.fixture()
def settings_store(monkeypatch):
    """An in-memory app_settings backing auth.get_setting/set_setting (no DB touched)."""
    import auth
    # seed recipients so send_alert has somewhere to deliver (transport is still
    # injected in the tests, so no SMTP host is needed); smtp_host stays unset so
    # the "unconfigured -> NOOP" path can be exercised by passing transport=None.
    store = {"notify_recipients": "ops@example.test"}
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(auth, "set_setting",
                        lambda k, v: store.__setitem__(k, str(v)))
    return store


def _set_events(monkeypatch, dlq=0, age_breach=False, deadlines=None):
    import waiting_room as WR
    import vat_refund as VR
    monkeypatch.setattr(WR, "queue_health",
                        lambda *a, **k: {"dlq": dlq, "age_breach": age_breach})
    monkeypatch.setattr(VR, "approaching_deadlines", lambda **k: deadlines or [])


def test_critical_alert_fires_then_dedups(monkeypatch, settings_store):
    import notify
    _set_events(monkeypatch, dlq=2, age_breach=False)
    fake = FakeTransport()

    # 1) first appearance -> SENT, fingerprint stored
    assert notify.critical_alert(transport=fake) is notify.SENT
    assert len(fake.sends) == 1
    assert fake.sends[0]["subject"] == "Fleet Fuel & VAT — CRITICAL"
    assert settings_store.get("notify_last_alert_fp")

    # 2) unchanged set -> NOOP, no second send (dedup)
    assert notify.critical_alert(transport=fake) is notify.NOOP
    assert len(fake.sends) == 1


def test_critical_alert_rearms_after_clear_then_rebreach(monkeypatch, settings_store):
    import notify
    fake = FakeTransport()

    _set_events(monkeypatch, dlq=1)
    assert notify.critical_alert(transport=fake) is notify.SENT
    assert len(fake.sends) == 1

    # set clears -> NOOP and the fingerprint is re-armed to ''
    _set_events(monkeypatch, dlq=0)
    assert notify.critical_alert(transport=fake) is notify.NOOP
    assert settings_store.get("notify_last_alert_fp") == ""

    # re-breach -> fires again (the re-arm is what allows this)
    _set_events(monkeypatch, dlq=1)
    assert notify.critical_alert(transport=fake) is notify.SENT
    assert len(fake.sends) == 2


def test_critical_alert_failed_does_not_advance_fp(monkeypatch, settings_store):
    import notify
    _set_events(monkeypatch, dlq=1)
    # transport fails -> FAILED, fingerprint NOT stored so it retries next tick
    assert notify.critical_alert(transport=RaisingTransport()) is notify.FAILED
    assert "notify_last_alert_fp" not in settings_store

    # next tick with a working transport: the same set still fires (was never deduped)
    fake = FakeTransport()
    assert notify.critical_alert(transport=fake) is notify.SENT
    assert len(fake.sends) == 1


def test_critical_alert_noop_when_unconfigured(monkeypatch, settings_store):
    import notify
    _set_events(monkeypatch, dlq=1)
    # no transport passed and no smtp_host in the store -> _settings_transport() None
    assert notify.critical_alert(transport=None) is notify.NOOP
    # send_alert returned NOOP, so the fingerprint must NOT advance (still un-alerted)
    assert "notify_last_alert_fp" not in settings_store


def test_critical_alert_no_events_noop(monkeypatch, settings_store):
    import notify
    _set_events(monkeypatch, dlq=0, age_breach=False)
    fake = FakeTransport()
    assert notify.critical_alert(transport=fake) is notify.NOOP
    assert fake.sends == []


# ---------------------------------------------------------------- admin SMTP UI
def _csrf(client, path="/admin"):
    body = client.get(path).get_data(as_text=True)
    m = re.search(r'name="_csrf" value="([^"]+)"', body)
    assert m, "admin page must seed a CSRF token"
    return m.group(1)


def test_admin_card_present(client):
    html = client.get("/admin").get_data(as_text=True)
    assert "Email notifications (SMTP)" in html
    # the password field is always blank with a keep-current placeholder
    assert "leave blank to keep current" in html
    assert 'value="set_smtp"' in html
    assert 'value="send_test_email"' in html


def test_set_smtp_persists_and_keeps_password(client):
    import auth
    auth.set_setting("smtp_pass", "secret-original")
    tok = _csrf(client)
    r = client.post("/admin", data={
        "_csrf": tok, "__act": "set_smtp",
        "smtp_host": "smtp.example.com", "smtp_port": "587",
        "smtp_user": "mailer", "smtp_from": "noreply@example.com",
        "notify_recipients": "ops@example.test, two@example.test",
        "notify_interval_hours": "12",
        "smtp_pass": "",                       # blank -> must NOT wipe the stored secret
    })
    assert r.status_code in (200, 302)
    assert auth.get_setting("smtp_host") == "smtp.example.com"
    assert auth.get_setting("smtp_port") == "587"
    assert auth.get_setting("notify_recipients") == "ops@example.test, two@example.test"
    assert auth.get_setting("notify_interval_hours") == "12"
    # the write-only secret survived a blank submit
    assert auth.get_setting("smtp_pass") == "secret-original"

    # a non-blank password DOES overwrite
    tok = _csrf(client)
    client.post("/admin", data={
        "_csrf": tok, "__act": "set_smtp", "smtp_host": "smtp.example.com",
        "smtp_pass": "new-secret"})
    assert auth.get_setting("smtp_pass") == "new-secret"

    # the stored password is never echoed back into the page
    assert "new-secret" not in client.get("/admin").get_data(as_text=True)


def test_set_smtp_blank_host_is_fine(client):
    import auth
    tok = _csrf(client)
    r = client.post("/admin", data={
        "_csrf": tok, "__act": "set_smtp", "smtp_host": "",
        "notify_recipients": "", "smtp_pass": ""})
    assert r.status_code in (200, 302)
    assert (auth.get_setting("smtp_host") or "") == ""


def test_send_test_email_noop_banner_no_host(client):
    import auth
    auth.set_setting("smtp_host", "")          # unconfigured -> NOOP, never raises
    auth.set_setting("notify_recipients", "")
    tok = _csrf(client)
    r = client.post("/admin", data={"_csrf": tok, "__act": "send_test_email"},
                    follow_redirects=True)
    assert r.status_code == 200
    assert "Nothing sent" in r.get_data(as_text=True)


def test_admin_smtp_blocked_for_processor(admin_session):
    import app as A
    import auth
    auth.add_user("smtp_proc", "Pw!23456", role="processor")
    c = A.app.test_client()
    c.post("/login", data={"username": "smtp_proc", "password": "Pw!23456"})
    assert c.get("/admin").status_code == 403
    # the state-changing POST is blocked too (403 admin-gate or 400 CSRF — a processor
    # can never obtain a valid token off a forbidden page; either way it never succeeds)
    assert c.post("/admin", data={"__act": "set_smtp",
                                  "smtp_host": "evil"}).status_code in (400, 403)
