"""
AI PIPELINE STATUS + FAILURE VISIBILITY — the three visible-status improvements:

  1. ai_verify.status() reports the PRECISE live state (ACTIVE vs the correct INACTIVE
     reason) computed from the gating conditions: setting off / no vision backend / no key.
  2. ai_verify.test_connection() makes ONE minimal real call and maps the provider error to
     a clear message (401 / credit / model / network), never exposing the API key, and
     returns ok on a good backend.
  3. extract surfaces a vision-capture BACKEND failure (when ENABLED) as a ⚠️ note on the
     fallback draft AND an admin error-log entry — while OFF stays silent / byte-identical.

The vision backend is MONKEYPATCHED throughout; no real key or network is used.
"""
import re

import pytest

import ai_verify
import ai_review
import vision_capture as VC
import extract as EX


# ============================================================ 1) status() line
def _key_env(monkeypatch, claude=None, openai=None):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    if claude is not None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", claude)
    if openai is not None:
        monkeypatch.setenv("OPENAI_API_KEY", openai)


def test_status_active_when_all_conditions_met(monkeypatch):
    monkeypatch.setattr(ai_verify, "_setting_on", lambda s: True)
    monkeypatch.setattr(ai_review, "resolve_backend", lambda *a, **k: "claude")
    _key_env(monkeypatch, claude="sk-secret-DO-NOT-LEAK")
    st = ai_verify.status()
    assert st["active"] is True
    assert st["provider"] == "claude"
    assert st["reason"] == ""
    assert "claude" in st["model"].lower()
    assert st["provider_label"]                       # human label present


def test_status_inactive_setting_off(monkeypatch):
    monkeypatch.setattr(ai_verify, "_setting_on", lambda s: False)
    monkeypatch.setattr(ai_review, "resolve_backend", lambda *a, **k: "claude")
    _key_env(monkeypatch, claude="sk-x")
    st = ai_verify.status("ai_verify_enabled")
    assert st["active"] is False
    assert "ai_verify_enabled" in st["reason"] and "off" in st["reason"].lower()


def test_status_inactive_backend_none(monkeypatch):
    monkeypatch.setattr(ai_verify, "_setting_on", lambda s: True)
    monkeypatch.setattr(ai_review, "resolve_backend", lambda *a, **k: "none")
    _key_env(monkeypatch, claude="sk-x")
    st = ai_verify.status()
    assert st["active"] is False
    assert "review backend" in st["reason"].lower()
    assert "'none'" in st["reason"]


def test_status_inactive_no_key(monkeypatch):
    monkeypatch.setattr(ai_verify, "_setting_on", lambda s: True)
    monkeypatch.setattr(ai_review, "resolve_backend", lambda *a, **k: "openai")
    _key_env(monkeypatch)                              # NO key for openai
    st = ai_verify.status()
    assert st["active"] is False
    assert "no api key" in st["reason"].lower()
    assert "OPENAI_API_KEY" in st["reason"]


def test_status_gate_order_setting_first(monkeypatch):
    # setting off wins even when backend+key are also missing
    monkeypatch.setattr(ai_verify, "_setting_on", lambda s: False)
    monkeypatch.setattr(ai_review, "resolve_backend", lambda *a, **k: "none")
    _key_env(monkeypatch)
    st = ai_verify.status()
    assert "off" in st["reason"].lower()


# ============================================================ 2) test_connection()
def test_test_connection_ok(monkeypatch):
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "claude")
    monkeypatch.setattr(ai_verify, "provider_label", lambda *a, **k: "Claude (Anthropic)")
    monkeypatch.setattr(ai_verify, "model_name", lambda *a, **k: "claude-opus-4-8")
    sent = {}

    def _call(prompt, data_str, images):
        sent["prompt"] = prompt
        sent["images"] = images
        return {"ok": True}

    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", _call)
    res = ai_verify.test_connection()
    assert res["ok"] is True
    assert "Claude" in res["message"]
    assert "claude-opus-4-8" in res["message"]
    assert sent["images"] == []                        # text-only: NO PDF/image sent


def test_test_connection_no_backend_no_call(monkeypatch):
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: None)
    monkeypatch.setattr(ai_verify, "status", lambda *a, **k: {"active": False, "reason": "X"})
    called = []
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", lambda *a, **k: called.append(1))
    monkeypatch.setitem(ai_verify._VISION_CALL, "openai", lambda *a, **k: called.append(1))
    res = ai_verify.test_connection()
    assert res["ok"] is False
    assert called == []                                # not configured => ZERO network call


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def _err(status_code=None, text="", msg="boom"):
    e = Exception(msg)
    if status_code is not None or text:
        e.response = _Resp(status_code, text)
    return e


@pytest.mark.parametrize("err,expect", [
    (_err(401, '{"error":"invalid api key"}'), "401 invalid API key"),
    (_err(None, "", "insufficient_quota: you exceeded your current quota"),
     "insufficient credit / quota — top up the account"),
    (_err(404, "model_not_found: the model does not exist"), "model not found"),
    (_err(None, "", "Connection timed out"), "network/timeout"),
])
def test_test_connection_maps_errors(monkeypatch, err, expect):
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "openai")
    monkeypatch.setattr(ai_verify, "provider_label", lambda *a, **k: "OpenAI")
    monkeypatch.setattr(ai_verify, "model_name", lambda *a, **k: "gpt-4o")

    def _boom(*a, **k):
        raise err

    monkeypatch.setitem(ai_verify._VISION_CALL, "openai", _boom)
    res = ai_verify.test_connection()
    assert res["ok"] is False
    assert expect in res["message"]


def test_test_connection_never_leaks_key(monkeypatch):
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "claude")
    monkeypatch.setattr(ai_verify, "provider_label", lambda *a, **k: "Claude")
    monkeypatch.setattr(ai_verify, "model_name", lambda *a, **k: "m")
    secret = "sk-ANT-SUPER-SECRET-KEY-123"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)

    def _boom(*a, **k):
        # an error whose text echoes the key would be a leak — the mapper must not surface it
        raise _err(401, f'{{"error":"invalid api key {secret}"}}')

    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", _boom)
    res = ai_verify.test_connection()
    assert secret not in res["message"]
    assert "401 invalid API key" in res["message"]


# ============================================================ 3) capture failure surfacing
def _enable_capture(monkeypatch):
    monkeypatch.setattr(VC, "enabled", lambda: True)
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: "claude")
    monkeypatch.setattr(VC, "provider_label", lambda *a, **k: "Claude (Anthropic)")
    # render returns 1 fake page so we reach the backend call
    monkeypatch.setattr(ai_verify, "_render_pages", lambda b, c: [b"PNG"])


def test_enabled_capture_backend_failure_surfaces_note_and_error_log(monkeypatch):
    _enable_capture(monkeypatch)
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda *a, **k: (_ for _ in ()).throw(_err(401, "invalid api key")))
    logged = []
    import auth
    monkeypatch.setattr(auth, "log_error",
                        lambda *a, **k: logged.append((a, k)))
    draft = EX.extract(b"%PDF-1.4 plain fake invoice", "scan.pdf", backend="parser")
    # (a) the fallback draft carries a loud ⚠️ note + the machine-readable reason
    assert draft["backend"] != "vision"               # fell back to OCR/parser path
    assert "⚠️ AI vision capture was ON but failed" in draft["notes"]
    assert draft.get("capture_failed")
    # (b) the admin error log got an entry with context "vision capture" + the reason
    assert logged, "auth.log_error was not called"
    args = logged[0][0]
    assert args[0] == "vision capture"
    reason = " ".join(str(x) for x in args)
    assert "401 invalid API key" in reason


def test_off_capture_no_note_no_error_log_no_call(monkeypatch):
    monkeypatch.setattr(VC, "enabled", lambda: False)
    called = []
    monkeypatch.setattr(VC, "capture", lambda *a, **k: called.append(1))
    logged = []
    import auth
    monkeypatch.setattr(auth, "log_error", lambda *a, **k: logged.append(a))
    draft = EX.extract(b"%PDF-1.4 plain fake invoice", "x.pdf", backend="parser")
    assert called == []                                # OFF => no capture() call at all
    assert logged == []                                # OFF => no error-log entry
    assert "AI vision capture was ON but failed" not in (draft.get("notes") or "")
    assert "capture_failed" not in draft


def test_enabled_capture_off_provider_is_silent(monkeypatch):
    # ENABLED setting per enabled(), but _provider() is None inside capture() (e.g. key
    # vanished): capture returns None WITHOUT setting a reason -> silent fallback, no note.
    monkeypatch.setattr(VC, "enabled", lambda: True)
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: None)
    logged = []
    import auth
    monkeypatch.setattr(auth, "log_error", lambda *a, **k: logged.append(a))
    draft = EX.extract(b"%PDF-1.4 plain fake invoice", "x.pdf", backend="parser")
    assert logged == []
    assert "AI vision capture was ON but failed" not in (draft.get("notes") or "")


def test_take_last_error_is_one_shot(monkeypatch):
    # a backend failure sets the reason; the next OFF capture clears it (no stale resurface)
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: "claude")
    monkeypatch.setattr(VC, "provider_label", lambda *a, **k: "Claude")
    monkeypatch.setattr(ai_verify, "_render_pages", lambda b, c: [b"PNG"])
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad json")))
    assert VC.capture(b"%PDF fake") is None
    r = VC.take_last_error()
    assert r and "Claude" in r
    assert VC.take_last_error() is None                # one-shot: cleared
    # an OFF capture does not resurface it
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: None)
    assert VC.capture(b"%PDF fake") is None
    assert VC.take_last_error() is None


# ============================================================ web: status line + test button (XSS)
def _tok(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_admin_status_line_active(client, monkeypatch):
    monkeypatch.setattr(ai_verify, "status",
                        lambda s=None: {"active": True, "provider": "claude",
                                        "provider_label": "Claude (Anthropic)",
                                        "model": "claude-opus-4-8", "reason": ""})
    html = client.get("/admin").get_data(as_text=True)
    assert "ACTIVE" in html
    assert "Test AI connection" in html


def test_admin_status_line_inactive_reason_escaped(client, monkeypatch):
    import app as A
    xss = "<img src=x onerror=alert(1)>"
    monkeypatch.setattr(ai_verify, "status",
                        lambda s=None: {"active": False, "provider": None,
                                        "provider_label": "", "model": "", "reason": xss})
    html = client.get("/admin").get_data(as_text=True)
    assert "INACTIVE" in html
    assert xss not in html                             # the reason is ESCAPED
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_admin_test_connection_button_ok(client, monkeypatch):
    monkeypatch.setattr(ai_verify, "test_connection",
                        lambda *a, **k: {"ok": True, "provider": "Claude",
                                         "model": "claude-opus-4-8", "message": "ok"})
    r = client.post("/admin", data={"_csrf": _tok(client, "/admin"),
                                    "__act": "test_ai_connection"})
    body = r.get_data(as_text=True)
    assert "OK" in body and "claude-opus-4-8" in body


def test_admin_test_connection_button_failure_escaped(client, monkeypatch):
    xss = "<b>401</b> <script>x</script>"
    monkeypatch.setattr(ai_verify, "test_connection",
                        lambda *a, **k: {"ok": False, "provider": "", "model": "",
                                         "message": xss})
    r = client.post("/admin", data={"_csrf": _tok(client, "/admin"),
                                    "__act": "test_ai_connection"})
    body = r.get_data(as_text=True)
    assert "failed" in body.lower()
    assert "<script>x</script>" not in body            # the mapped message is ESCAPED
    assert "&lt;script&gt;x&lt;/script&gt;" in body
