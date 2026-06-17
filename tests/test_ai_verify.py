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


# A draft that carries a FULL vision `capture` document (the vision_capture.to_draft shape).
CAPTURE = {
    "header": {
        "supplier": {"name": "DKV", "vat_number": "LV40003XXXX",
                     "address": "Riga 1", "country": "Latvia"},
        "customer": {"name": "ACME OU", "vat_number": "EE100", "account_or_card_no": "C-7"},
        "invoice": {"number": "S-9", "issue_date": "2026-05-31", "due_date": "2026-06-30",
                    "currency": "EUR", "exchange_rate": 1.0},
    },
    "lines": [
        {"date": "2026-05-30", "time": "08:12", "station_name": "Shell Antwerp",
         "city": "Antwerp", "country": "Belgium", "product": "Diesel", "quantity": 500.0,
         "unit": "L", "unit_price": 1.55, "discount": 5.0, "net": 775.0, "vat_rate": 21.0,
         "vat": 162.75, "gross": 937.75, "card_no": "CARD-1", "receipt_no": "R-100"},
        {"date": "2026-05-31", "time": "09:00", "station_name": "Total Liege",
         "city": "Liege", "country": "Belgium", "product": "AdBlue", "quantity": 30.0,
         "unit": "L", "unit_price": 0.90, "discount": 0.0, "net": 27.0, "vat_rate": 21.0,
         "vat": 5.67, "gross": 32.67, "card_no": "CARD-1", "receipt_no": "R-101"},
    ],
    "totals": {"net_total": 802.0, "discount_total": 5.0, "vat_total": 168.42,
               "gross_total": 970.42},
}
CAPTURE_DRAFT = {
    "supplier": "DKV", "supplier_vat": "LV40003XXXX", "statement_ref": "S-9",
    "statement_date": "2026-05-31", "currency": "EUR", "customer": "ACME OU",
    "lines": [],          # the rich data lives under `capture`
    "capture": CAPTURE,
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


# --------------------------------------------------------------- capture-document payload
def test_capture_payload_carries_full_capture_document(monkeypatch):
    """When the draft has a `capture` key, verify() sends the FULL capture document — rich
    header fields, EVERY line with all its fields, and the totals — NOT the condensed
    summary."""
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "claude")
    monkeypatch.setattr(ai_verify, "model_name", lambda be: "claude-opus-4-8")
    _patch_render(monkeypatch, 1)
    sent = {}

    def _call(prompt, data_str, images):
        sent["data"] = data_str
        return {"verdict": "confirmed", "fields": [], "notes": "ok"}

    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", _call)
    out = ai_verify.verify(b"%PDF fake", CAPTURE_DRAFT)
    assert out["verdict"] == "confirmed"
    d = sent["data"]
    # header (incl. due_date + exchange_rate) and customer present
    assert "2026-06-30" in d and "exchange_rate" in d and "ACME OU" in d
    # per-line rich fields present (both lines, all field kinds)
    for needle in ("Shell Antwerp", "Total Liege", "unit_price", "1.55",
                   "card_no", "CARD-1", "receipt_no", "R-100", "937.75", "vat_rate"):
        assert needle in d, needle
    # totals present
    assert "gross_total" in d and "970.42" in d
    # it is the capture document, not the condensed summary shape
    assert "statement_ref" not in d        # summary-only key
    assert "header" in d and "totals" in d


def test_capture_payload_builder_is_the_full_document():
    payload = ai_verify._verify_payload(CAPTURE_DRAFT)
    assert payload is CAPTURE             # the full capture document, verbatim
    assert payload["header"]["invoice"]["due_date"] == "2026-06-30"
    assert payload["lines"][0]["station_name"] == "Shell Antwerp"


def test_summary_payload_when_no_capture_key():
    """Backward compat: a deterministic/parser/OCR draft (no `capture`) still gets the
    condensed summary, not the capture-document shape."""
    payload = ai_verify._verify_payload(DRAFT)
    assert "statement_ref" in payload and payload["statement_ref"] == "S-9"
    assert "header" not in payload and "totals" not in payload
    assert payload["gross_total"] == 1210.0


def test_verify_summary_path_backward_compatible(monkeypatch):
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "claude")
    monkeypatch.setattr(ai_verify, "model_name", lambda be: "claude-opus-4-8")
    _patch_render(monkeypatch, 1)
    sent = {}
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda p, d, images: sent.update(data=d) or
                        {"verdict": "confirmed", "fields": [], "notes": ""})
    ai_verify.verify(b"%PDF fake", DRAFT)         # no `capture` key
    d = sent["data"]
    assert "S-9" in d and "DKV" in d
    assert "header" not in d and "due_date" not in d     # the summary, not the capture doc


def test_prompt_states_pdf_is_authoritative():
    p = ai_verify.VERIFY_PROMPT.lower()
    assert "source of truth" in p
    assert "the pdf is correct" in p
    assert "do not invent" in p
    # the rich, per-field verdict contract is requested
    assert "captured value" in p and "match" in p


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


# --------------------------------------------------------------- richer panel rendering
def test_panel_groups_and_escapes_rich_fields():
    """The panel renders rich header/line/totals fields, grouped, captured-vs-PDF, all
    escaped (untrusted model output)."""
    import app as A
    xss = '<img src=x onerror=alert(1)>'
    result = {
        "verdict": "discrepancies",
        "fields": [
            {"name": "invoice.due_date", "extracted": "2026-06-30",
             "document": "2026-07-01", "match": False},
            {"name": "line[2].vat", "extracted": "5.67", "document": xss, "match": False},
            {"name": "totals.gross", "extracted": "970.42", "document": "970.42",
             "match": True},
        ],
        "notes": xss, "provider": "claude", "model": "claude-opus-4-8", "pages": 2,
    }
    html = A._ai_verify_panel(result)
    # section headers present
    assert "Header" in html and "Lines" in html and "Totals" in html
    # rich field names rendered
    assert "invoice.due_date" in html and "line[2].vat" in html and "totals.gross" in html
    # captured-vs-PDF wording + the authoritative-PDF framing
    assert "captured" in html and "PDF shows" in html
    # the planted XSS (in a document value AND notes) is escaped, never a live tag
    assert xss not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


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


def test_verify_panel_links_correction_action_when_enabled(client, monkeypatch):
    """When the verdict is `discrepancies` and the feature is enabled, the verify panel
    offers the 'Apply AI corrections & re-verify' action (the entry to the correction loop).
    A confirmed verdict offers no such action (nothing to correct)."""
    import app as A, ai_verify as V
    monkeypatch.setattr(V, "enabled", lambda: True)
    monkeypatch.setattr(V, "provider_label", lambda *a, **k: "Claude (Anthropic)")
    disc = {"verdict": "discrepancies", "provider": "claude", "model": "m", "pages": 1,
            "notes": "", "fields": [{"name": "totals.gross", "extracted": "1",
                                     "document": "2", "match": False}]}
    with A.app.test_request_context("/extract"):
        html = A._ai_verify_panel(disc, token="t", intake_job="1", period="2026-05")
        ok = {"verdict": "confirmed", "fields": [], "notes": "", "provider": "claude",
              "model": "m", "pages": 1}
        html_ok = A._ai_verify_panel(ok, token="t", intake_job="1", period="2026-05")
    assert 'action="/extract/ai-correct"' in html
    assert 'action="/extract/ai-correct"' not in html_ok


def test_apply_corrections_is_advisory_does_not_mutate_input(monkeypatch):
    """The correction loop edits a COPY — apply_corrections never mutates the input draft."""
    import copy as _copy
    d = _copy.deepcopy(CAPTURE_DRAFT)
    d["lines"] = [{"net": 775.0, "vat": 162.75}, {"net": 27.0, "vat": 5.67}]
    before = _copy.deepcopy(d)
    verdict = {"verdict": "discrepancies", "fields": [
        {"name": "totals.gross", "extracted": "970.42", "document": "971.00",
         "match": False}], "notes": ""}
    corrected, corrections = ai_verify.apply_corrections(d, verdict)
    assert d == before                                  # input untouched
    assert corrected["capture"]["totals"]["gross_total"] == 971.0
    assert corrections and corrections[0]["field"] == "totals.gross"


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
