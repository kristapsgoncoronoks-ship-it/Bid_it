"""
AI DOCUMENT ASSISTANT (advisory chat-with-document) — the hard privacy invariants and the
web surface.

NO live API is ever called: `ai_assistant._call_text` is monkeypatched. The tests assert:
  * `enabled()` is False when the setting is OFF OR no backend is configured;
  * with both ON, `ask()` returns the (mocked) answer and BOTH messages persist;
  * the DERIVED context passed to the backend carries the extracted fields but NO
    IBAN / account / secret (a planted IBAN/account/secret is absent from the payload);
  * a backend exception is caught (never raises) and surfaces a graceful message;
  * the UI panel shows the disabled notice when off and makes NO backend call;
  * advisory-only: nothing in the module mutates a figure/status (it only reads + returns text).
"""
import re

import pytest

import ai_assistant


# A subject descriptor deliberately carrying SECRET/bank material that must NEVER reach the
# model — build_context must strip it (reusing ai_review's redactor).
SUBJECT = {
    "kind": "invoice", "supplier": "DKV", "supplier_vat": "LV40003XXXX",
    "statement_ref": "INV-77", "statement_date": "2026-05-31", "currency": "EUR",
    "customer": "Jupiter Plus AS",
    "iban": "LV80BANK0000435195001",          # must be stripped
    "account_number": "1234567890",            # must be stripped
    "secret": "hunter2",                       # must be stripped
    "_pdf_bytes": b"%PDF-1.7 ...",             # must be stripped (private key)
    "lines": [
        {"invoice_no": "BE001", "date": "2026-05-31", "country": "Belgium",
         "currency": "EUR", "net": 1000.0, "vat": 210.0,
         "iban": "LV99NEVER", "_source": "doc.pdf"},
    ],
}


@pytest.fixture()
def tmp_chat_db(tmp_path, monkeypatch):
    """Point the chat DB at a throwaway file so persistence tests never touch a real DB."""
    monkeypatch.setattr(ai_assistant, "DB", str(tmp_path / "ai_chat.db"))
    return ai_assistant.DB


# --------------------------------------------------------------- enabled() gating
def test_enabled_false_when_setting_off(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "get_setting",
                        lambda k, d=None: "off" if k == ai_assistant.SETTING else d)
    monkeypatch.setattr(ai_assistant, "backend", lambda *a, **k: "claude")  # backend present
    assert ai_assistant.enabled() is False


def test_enabled_false_when_no_backend(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "get_setting",
                        lambda k, d=None: "on" if k == ai_assistant.SETTING else d)
    monkeypatch.setattr(ai_assistant, "backend", lambda *a, **k: "none")    # no backend
    assert ai_assistant.enabled() is False


def test_enabled_true_when_setting_on_and_backend(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "get_setting",
                        lambda k, d=None: "on" if k == ai_assistant.SETTING else d)
    monkeypatch.setattr(ai_assistant, "backend", lambda *a, **k: "claude")
    assert ai_assistant.enabled() is True


# --------------------------------------------------------------- derived context / privacy
def test_build_context_has_extracted_fields_but_no_secret():
    ctx = ai_assistant.build_context(SUBJECT)
    flat = repr(ctx)
    # extracted/derived fields ARE present
    assert ctx["supplier"] == "DKV"
    assert ctx["statement_ref"] == "INV-77"
    assert ctx["lines"][0]["invoice_no"] == "BE001"
    assert ctx["lines"][0]["country"] == "Belgium"
    # NO bank/account/secret value anywhere in the payload
    for secret in ("LV80BANK0000435195001", "1234567890", "hunter2", "LV99NEVER", "%PDF"):
        assert secret not in flat, f"secret {secret!r} leaked into derived context"
    # no '_'-prefixed key anywhere
    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                assert not str(k).startswith("_"), f"leaked private key {k}"
                keys(v)
        elif isinstance(o, list):
            for v in o:
                keys(v)
    keys(ctx)


def test_ask_outbound_payload_strips_iban_and_secret(monkeypatch):
    """The string actually handed to the backend must contain the extracted fields but NONE
    of the planted IBAN/account/secret values."""
    seen = {}

    def _fake_call(be, prompt, content_str):
        seen["content"] = content_str
        return "It is a Belgium fuel invoice for 1000.00 net."

    monkeypatch.setattr(ai_assistant, "_call_text", _fake_call)
    monkeypatch.setattr(ai_assistant, "backend", lambda *a, **k: "claude")
    ctx = ai_assistant.build_context(SUBJECT)
    out = ai_assistant.ask(ctx, "What country is this invoice for?")
    assert "Belgium" in out
    payload = seen["content"]
    assert "Belgium" in payload and "BE001" in payload          # derived data present
    for secret in ("LV80BANK0000435195001", "1234567890", "hunter2", "LV99NEVER", "%PDF"):
        assert secret not in payload, f"secret {secret!r} leaked into the outbound payload"


# --------------------------------------------------------------- ask() behaviour
def test_ask_no_backend_makes_no_call(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_assistant, "_call_text",
                        lambda *a, **k: calls.append(1) or "x")
    monkeypatch.setattr(ai_assistant, "backend", lambda *a, **k: "none")
    out = ai_assistant.ask({"lines": []}, "anything?")
    assert calls == []                                          # ZERO network calls
    assert "disabled" in out.lower()


def test_ask_returns_mocked_answer(monkeypatch):
    monkeypatch.setattr(ai_assistant, "_call_text",
                        lambda *a, **k: "The total net is 1000.00 EUR.")
    monkeypatch.setattr(ai_assistant, "backend", lambda *a, **k: "openai")
    out = ai_assistant.ask(ai_assistant.build_context(SUBJECT), "What is the net?")
    assert out == "The total net is 1000.00 EUR."


def test_ask_catches_backend_exception_and_never_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("model exploded")
    monkeypatch.setattr(ai_assistant, "_call_text", _boom)
    monkeypatch.setattr(ai_assistant, "backend", lambda *a, **k: "claude")
    out = ai_assistant.ask({"lines": []}, "hello?")          # must not raise
    assert "unavailable" in out.lower()
    assert "advisory only" in out.lower()


def test_ask_transient_error_graceful(monkeypatch):
    from extract import TransientExtractionError
    def _boom(*a, **k):
        raise TransientExtractionError("429 rate limited")
    monkeypatch.setattr(ai_assistant, "_call_text", _boom)
    monkeypatch.setattr(ai_assistant, "backend", lambda *a, **k: "claude")
    out = ai_assistant.ask({"lines": []}, "hello?")
    assert "try again" in out.lower()


# --------------------------------------------------------------- chat-history persistence
def test_both_messages_persist(tmp_chat_db, monkeypatch):
    monkeypatch.setattr(ai_assistant, "_call_text", lambda *a, **k: "An answer.")
    monkeypatch.setattr(ai_assistant, "backend", lambda *a, **k: "claude")
    ref = "doc:42"
    cid = ai_assistant.start_chat(ref, created_by="pytest")
    assert cid is not None
    ai_assistant.record_message(cid, "user", "What is this?")
    answer = ai_assistant.ask(ai_assistant.build_context(SUBJECT), "What is this?")
    ai_assistant.record_message(cid, "assistant", answer)
    msgs = ai_assistant.messages_for(ref)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "What is this?"
    assert msgs[1]["content"] == "An answer."
    # start_chat reuses the latest thread for the same subject (no duplicate thread)
    assert ai_assistant.start_chat(ref) == cid


# --------------------------------------------------------------- web surface
def _tok(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_admin_persists_doc_chat_toggle(client):
    import auth
    auth.set_setting("ai_doc_chat_enabled", "off")
    client.post("/admin", data={"_csrf": _tok(client, "/admin"),
                                "__act": "set_ai_doc_chat", "ai_doc_chat_enabled": "on"})
    assert auth.get_setting("ai_doc_chat_enabled") == "on"
    # unchecked -> off
    client.post("/admin", data={"_csrf": _tok(client, "/admin"),
                                "__act": "set_ai_doc_chat"})
    assert auth.get_setting("ai_doc_chat_enabled") == "off"


def test_admin_card_renders(client):
    html = client.get("/admin").get_data(as_text=True)
    assert "AI document assistant" in html
    assert 'name="ai_doc_chat_enabled"' in html


def test_doc_assistant_disabled_shows_notice_and_no_call(client, monkeypatch):
    import app as A
    # a fixed derived subject so the page doesn't need a real vaulted document
    monkeypatch.setattr(A, "_doc_subject",
                        lambda doc_id: (dict(SUBJECT), {"supplier": "DKV", "invoice_ref": "BE001",
                                                        "entity": "Jupiter Plus AS"}))
    # force OFF + assert NO backend call is ever made
    monkeypatch.setattr(ai_assistant, "enabled", lambda: False)
    called = []
    monkeypatch.setattr(ai_assistant, "_call_text",
                        lambda *a, **k: called.append(1) or "x")
    r = client.post("/doc-assistant/1",
                    data={"_csrf": _tok(client, "/admin"), "question": "what is this?"})
    html = r.get_data(as_text=True)
    assert "Disabled" in html
    assert "advisory only" in html.lower()
    assert called == []                                        # NO backend call when off


def test_doc_assistant_enabled_renders_answer_and_escapes(client, tmp_chat_db, monkeypatch):
    import app as A
    monkeypatch.setattr(A, "_doc_subject",
                        lambda doc_id: (dict(SUBJECT), {"supplier": "DKV", "invoice_ref": "BE001",
                                                        "entity": "Jupiter Plus AS"}))
    monkeypatch.setattr(ai_assistant, "enabled", lambda: True)
    monkeypatch.setattr(ai_assistant, "backend", lambda *a, **k: "claude")
    injection = "<img src=x onerror=alert(1)>"
    monkeypatch.setattr(ai_assistant, "_call_text", lambda *a, **k: injection)
    r = client.post("/doc-assistant/2",
                    data={"_csrf": _tok(client, "/admin"), "question": "describe it"})
    html = r.get_data(as_text=True)
    # the model's answer is ESCAPED, never a live tag
    assert injection not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    # the question we asked is shown in the transcript
    assert "describe it" in html
    # the standing disclaimer is present
    assert "never changes any figure" in html
