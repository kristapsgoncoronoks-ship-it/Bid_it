"""
AI VERIFICATION (vision) — the OPT-IN, ADVISORY check that compares an extracted draft
against the ORIGINAL PDF page images. The vision backend is MONKEYPATCHED throughout; no
real key or network is used.

Load-bearing invariants asserted here:
  * OFF (setting off OR no key) => enabled() False AND verify() makes NO backend call.
  * enabled => verify() sends the page image(s) + draft and parses the verdict
    (confirmed + a discrepancy case).
  * a backend exception/timeout => "unavailable", never raises.
  * the page cap is honoured (a 10-page PDF sends <= MAX_PAGES images).
  * ADVISORY — verify() never mutates the draft (no figure/draft write path).
  * the review screen renders the verdict and ESCAPES a planted XSS string in the model's
    returned notes/document fields.
"""
import re

import pytest

import ai_verify


# --------------------------------------------------------------- helpers
class _FakeImg:
    def save(self, buf, format=None):
        buf.write(b"PNG-bytes")


def _patch_render(monkeypatch, npages):
    """Make _render_pages return `npages` fake PNGs WITHOUT touching pdf2image. It honours
    the cap exactly as the real one does (slice to cap)."""
    def _render(pdf_bytes, max_pages):
        cap = max(1, int(max_pages or ai_verify.MAX_PAGES))
        return [b"PNG-bytes"] * min(npages, cap)
    monkeypatch.setattr(ai_verify, "_render_pages", _render)


DRAFT = {
    "supplier": "DKV", "supplier_vat": "LV40003XXXX", "statement_ref": "S-9",
    "statement_date": "2026-05-31", "currency": "EUR",
    "lines": [{"invoice_no": "BE001", "date": "2026-05-31", "country": "Belgium",
               "currency": "EUR", "net": 1000.0, "vat": 210.0, "_source": "doc.pdf"}],
}


# --------------------------------------------------------------- enabled() gating
def test_enabled_false_when_setting_off(monkeypatch):
    import auth
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: "off" if k == ai_verify.SETTING else d)
    assert ai_verify.enabled() is False


def test_enabled_false_when_no_key(monkeypatch):
    import auth
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: "on" if k == ai_verify.SETTING else d)
    assert ai_verify.enabled() is False           # ON but no key => OFF


def test_enabled_false_when_backend_not_vision(monkeypatch):
    import auth
    monkeypatch.setenv("AZURE_OPENAI_KEY", "x")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "azure")
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: "on" if k == ai_verify.SETTING else d)
    assert ai_verify.enabled() is False           # azure is not vision-gated here


def test_enabled_true_when_on_and_key(monkeypatch):
    import auth
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "openai")
    monkeypatch.setattr(auth, "get_setting", lambda k, d=None: "on" if k == ai_verify.SETTING else d)
    assert ai_verify.enabled() is True


# --------------------------------------------------------------- OFF => no backend call
def test_off_makes_no_backend_call(monkeypatch):
    # no provider configured -> verify() returns unavailable WITHOUT ever calling a backend
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: None)
    called = []
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda *a, **k: called.append(1))
    monkeypatch.setitem(ai_verify._VISION_CALL, "openai",
                        lambda *a, **k: called.append(1))
    out = ai_verify.verify(b"%PDF-1.4 fake", DRAFT)
    assert out["verdict"] == "unavailable"
    assert called == []                           # NEVER a network call when off


# --------------------------------------------------------------- verify() happy paths
def test_verify_confirmed(monkeypatch):
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "claude")
    monkeypatch.setattr(ai_verify, "model_name", lambda be: "claude-opus-4-8")
    _patch_render(monkeypatch, 1)
    sent = {}

    def _call(prompt, data_str, images):
        sent["images"] = images
        sent["data"] = data_str
        return {"verdict": "confirmed",
                "fields": [{"name": "supplier", "extracted": "DKV",
                            "document": "DKV", "match": True}],
                "notes": "All good."}

    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", _call)
    out = ai_verify.verify(b"%PDF fake", DRAFT)
    assert out["verdict"] == "confirmed"
    assert out["provider"] == "claude"
    assert out["pages"] == 1
    # the page image(s) AND the draft were sent
    assert sent["images"] == [b"PNG-bytes"]
    assert "DKV" in sent["data"] and "S-9" in sent["data"]


def test_verify_discrepancies(monkeypatch):
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "openai")
    monkeypatch.setattr(ai_verify, "model_name", lambda be: "gpt-4o")
    _patch_render(monkeypatch, 1)

    def _call(prompt, data_str, images):
        return {"verdict": "discrepancies",
                "fields": [{"name": "vat", "extracted": "210.00",
                            "document": "201.00", "match": False}],
                "notes": "VAT differs."}

    monkeypatch.setitem(ai_verify._VISION_CALL, "openai", _call)
    out = ai_verify.verify(b"%PDF fake", DRAFT)
    assert out["verdict"] == "discrepancies"
    assert out["fields"][0]["match"] is False
    assert out["fields"][0]["document"] == "201.00"


# --------------------------------------------------------------- transient => unavailable
def test_backend_exception_returns_unavailable(monkeypatch):
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "claude")
    _patch_render(monkeypatch, 1)

    def _boom(prompt, data_str, images):
        raise RuntimeError("429 rate limit exceeded")

    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", _boom)
    out = ai_verify.verify(b"%PDF fake", DRAFT)        # must NOT raise
    assert out["verdict"] == "unavailable"


def test_hard_backend_error_returns_unavailable(monkeypatch):
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "claude")
    _patch_render(monkeypatch, 1)
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad json")))
    out = ai_verify.verify(b"%PDF fake", DRAFT)        # never raises
    assert out["verdict"] == "unavailable"


# --------------------------------------------------------------- page cap
def test_page_cap_honoured(monkeypatch):
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "claude")
    _patch_render(monkeypatch, 10)                     # a 10-page PDF
    sent = {}
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda p, d, images: sent.update(n=len(images)) or
                        {"verdict": "confirmed", "fields": [], "notes": ""})
    ai_verify.verify(b"%PDF fake", DRAFT)
    assert sent["n"] <= ai_verify.MAX_PAGES
    assert sent["n"] == ai_verify.MAX_PAGES            # capped to exactly MAX_PAGES


def test_render_pages_caps_with_real_helper(monkeypatch):
    """The real _render_pages must pass a bounded last_page AND slice to the cap, so even a
    convert_from_bytes that ignores last_page never returns more than MAX_PAGES images."""
    captured = {}

    def _fake_convert(pdf_bytes, first_page=None, last_page=None):
        captured["last_page"] = last_page
        return [_FakeImg() for _ in range(10)]         # 10 pages, ignoring last_page

    import sys, types
    fake_mod = types.ModuleType("pdf2image")
    fake_mod.convert_from_bytes = _fake_convert
    monkeypatch.setitem(sys.modules, "pdf2image", fake_mod)
    pngs = ai_verify._render_pages(b"%PDF", ai_verify.MAX_PAGES)
    assert captured["last_page"] == ai_verify.MAX_PAGES
    assert len(pngs) == ai_verify.MAX_PAGES


# --------------------------------------------------------------- ADVISORY (no mutation)
def test_verify_does_not_mutate_draft(monkeypatch):
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "claude")
    _patch_render(monkeypatch, 1)
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda *a, **k: {"verdict": "discrepancies",
                                         "fields": [{"name": "net", "extracted": "1000",
                                                     "document": "999", "match": False}],
                                         "notes": "off"})
    import copy
    before = copy.deepcopy(DRAFT)
    ai_verify.verify(b"%PDF fake", DRAFT)
    assert DRAFT == before                             # the draft is untouched


def test_verify_module_has_no_db_or_figure_write():
    """Structural guard: ai_verify is read-only — it imports no product-DB writer and has no
    connect()/INSERT/UPDATE path. (verify() only reads + returns.)"""
    import inspect
    src = inspect.getsource(ai_verify)
    assert "def connect(" not in src
    for forbidden in ("INSERT", "UPDATE ", "DELETE FROM", "ln[", '["net"] ='):
        assert forbidden not in src


# --------------------------------------------------------------- parse_verdict robustness
def test_parse_verdict_coerces_unknown():
    out = ai_verify.parse_verdict({"verdict": "bogus", "fields": "nope", "notes": 5})
    assert out["verdict"] == "unreadable"
    assert out["fields"] == [] and out["notes"] == ""


def test_parse_verdict_drops_malformed_fields():
    out = ai_verify.parse_verdict(
        {"verdict": "confirmed",
         "fields": [{"name": "", "match": True},          # empty name dropped
                    "garbage",                            # non-dict dropped
                    {"name": "vat", "extracted": None, "document": "1", "match": "yes"}],
         "notes": "ok"})
    assert len(out["fields"]) == 1
    assert out["fields"][0]["name"] == "vat"
    assert out["fields"][0]["extracted"] == ""           # None -> ""
    assert out["fields"][0]["match"] is True             # truthy -> bool


# --------------------------------------------------------------- web surface
def _tok(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_admin_card_and_toggle(client):
    import auth
    html = client.get("/admin").get_data(as_text=True)
    assert "AI verification against the original PDF" in html
    assert "ORIGINAL invoice PDF" in html                # the loud warning
    assert 'name="ai_verify_enabled"' in html
    # persist via the form
    client.post("/admin", data={"_csrf": _tok(client, "/admin"),
                                "__act": "set_ai_verify", "ai_verify_enabled": "on"})
    assert str(auth.get_setting("ai_verify_enabled")).lower() in ("on", "1", "true", "yes")
    client.post("/admin", data={"_csrf": _tok(client, "/admin"),
                                "__act": "set_ai_verify"})        # unchecked => off
    assert str(auth.get_setting("ai_verify_enabled")).lower() in ("off", "0", "false", "no")


def test_review_screen_renders_verdict_and_escapes(client, monkeypatch):
    import app as A, ai_verify as V
    # force the feature ON + a fake job whose PDF we don't actually read (verify mocked)
    monkeypatch.setattr(V, "enabled", lambda: True)
    injection = '<img src=x onerror=alert(1)>'
    monkeypatch.setattr(V, "verify", lambda pdf, draft, **k: {
        "verdict": "discrepancies",
        "fields": [{"name": "supplier", "extracted": injection,
                    "document": injection, "match": False}],
        "notes": injection, "provider": "claude", "model": "claude-opus-4-8", "pages": 2})

    class _Job(dict):
        pass
    import waiting_room as IQ, extract as EX
    monkeypatch.setattr(IQ, "get_job", lambda j: {"id": 1, "stored_path": "x",
                                                  "filename": "f.pdf", "status": "ready"})
    monkeypatch.setattr(IQ, "read_bytes", lambda p: b"%PDF fake")
    monkeypatch.setattr(EX, "unpack", lambda b, n: [("f.pdf", b"%PDF fake")])

    A._stash_draft("vtok", DRAFT)
    r = client.post("/extract/ai-verify",
                    data={"_csrf": _tok(client, "/extract"), "token": "vtok",
                          "intake_job": "1", "period": "2026-05"})
    html = r.get_data(as_text=True)
    assert "AI verification against the PDF" in html
    assert "Discrepancies found" in html
    # the planted XSS in notes AND document is ESCAPED, never a live tag
    assert injection not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    # the confirm (commit) form is still present and unaffected
    assert 'action="/extract/confirm"' in html


def test_review_off_makes_no_verify_call(client, monkeypatch):
    import app as A, ai_verify as V
    monkeypatch.setattr(V, "enabled", lambda: False)
    called = []
    monkeypatch.setattr(V, "verify", lambda *a, **k: called.append(1) or {})
    A._stash_draft("offtok", DRAFT)
    r = client.post("/extract/ai-verify",
                    data={"_csrf": _tok(client, "/extract"), "token": "offtok",
                          "intake_job": "1", "period": "2026-05"})
    assert called == []                                   # OFF => no verify() call
    # falls back to the plain review form (no verdict panel)
    assert "AI verification against the PDF" not in r.get_data(as_text=True)
