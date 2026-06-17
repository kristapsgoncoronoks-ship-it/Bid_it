"""
AI CORRECTION + RE-VERIFY loop — apply_corrections() writes the PDF-authoritative value
back into the PRE-commit draft (capture document AND the mapped draft field registration
consumes), then the draft is auto re-verified. The vision backend is MONKEYPATCHED; no key
or network is used.

Load-bearing invariants asserted here:
  * apply_corrections updates a mismatched line[i].vat AND totals.gross in BOTH the capture
    doc and the mapped draft line, records was/now, and does NOT mutate the input draft.
  * an unknown/unresolvable field path is SKIPPED (not applied, still flagged).
  * an empty/unparseable PDF value is NOT applied (never blanks a field).
  * corrected amounts go through money.f2.
  * the web loop persists corrections + status, re-verifies (mocked) -> confirmed, renders
    the corrections log + the ✅ status, and ESCAPES a planted XSS in a corrected value.
  * nothing registers without the explicit Confirm (the correct/verify routes commit nothing).
  * OFF / no-provider => the correct action is unavailable.
"""
import re
import copy

import pytest

import ai_verify


# A draft carrying a FULL vision capture document (the vision_capture.to_draft shape), with
# the draft-line keys the rest of the pipeline reads ALSO present (as to_draft projects them).
def _draft():
    return {
        "supplier": "DKV", "supplier_vat": "LV40003XXXX", "statement_ref": "S-9",
        "statement_date": "2026-05-31", "currency": "EUR", "customer": "ACME OU",
        "lines": [
            {"invoice_no": "S-9", "date": "2026-05-30", "country": "Belgium",
             "currency": "EUR", "net": 775.0, "vat": 162.75, "product": "Diesel",
             "qty": 500.0, "_source": "vision", "vat_rate": 21.0, "gross": 937.75},
            {"invoice_no": "S-9", "date": "2026-05-31", "country": "Belgium",
             "currency": "EUR", "net": 27.0, "vat": 5.67, "product": "AdBlue",
             "qty": 30.0, "_source": "vision", "vat_rate": 21.0, "gross": 32.67},
        ],
        "capture": {
            "header": {
                "supplier": {"name": "DKV", "vat_number": "LV40003XXXX",
                             "address": "Riga 1", "country": "Latvia"},
                "customer": {"name": "ACME OU", "vat_number": "EE100",
                             "account_or_card_no": "C-7"},
                "invoice": {"number": "S-9", "issue_date": "2026-05-31",
                            "due_date": "2026-06-30", "currency": "EUR",
                            "exchange_rate": 1.0},
            },
            "lines": [
                {"date": "2026-05-30", "country": "Belgium", "product": "Diesel",
                 "quantity": 500.0, "net": 775.0, "vat_rate": 21.0, "vat": 162.75,
                 "gross": 937.75},
                {"date": "2026-05-31", "country": "Belgium", "product": "AdBlue",
                 "quantity": 30.0, "net": 27.0, "vat_rate": 21.0, "vat": 5.67,
                 "gross": 32.67},
            ],
            "totals": {"net_total": 802.0, "discount_total": 0.0, "vat_total": 168.42,
                       "gross_total": 970.42},
        },
    }


def _verdict(fields):
    return {"verdict": "discrepancies", "fields": fields, "notes": "",
            "provider": "claude", "model": "claude-opus-4-8", "pages": 2}


# --------------------------------------------------------------- apply_corrections core
def test_corrects_line_vat_and_totals_gross_in_capture_and_draft():
    d = _draft()
    before = copy.deepcopy(d)
    verdict = _verdict([
        {"name": "line[2].vat", "extracted": "5.67", "document": "5.70", "match": False},
        {"name": "totals.gross", "extracted": "970.42", "document": "970.45", "match": False},
    ])
    corrected, corrections = ai_verify.apply_corrections(d, verdict)
    # input draft NOT mutated in place
    assert d == before
    # capture doc updated (line index 2 = 1-based -> the SECOND line)
    assert corrected["capture"]["lines"][1]["vat"] == 5.70
    assert corrected["capture"]["totals"]["gross_total"] == 970.45
    # mapped DRAFT line updated too (so registration consumes the corrected value)
    assert corrected["lines"][1]["vat"] == 5.70
    # corrections log records was/now/source
    by = {c["field"]: c for c in corrections}
    assert by["line[2].vat"]["was"] == 5.67 and by["line[2].vat"]["now"] == 5.70
    assert by["line[2].vat"]["source"] == "ai-verify(PDF)"
    assert by["totals.gross"]["now"] == 970.45
    assert len(corrections) == 2


def test_amounts_go_through_money_f2():
    d = _draft()
    # a PDF value with > 2 decimals must be quantized half-up via money.f2
    verdict = _verdict([{"name": "line[1].net", "extracted": "775.00",
                         "document": "775.005", "match": False}])
    corrected, corrections = ai_verify.apply_corrections(d, verdict)
    import money
    assert corrected["capture"]["lines"][0]["net"] == money.f2(775.005) == 775.01
    assert corrected["lines"][0]["net"] == 775.01


def test_unknown_path_is_skipped_not_applied():
    d = _draft()
    before = copy.deepcopy(d)
    verdict = _verdict([
        {"name": "mystery.weird_field", "extracted": "x", "document": "y", "match": False},
        {"name": "line[99].vat", "extracted": "1", "document": "2", "match": False},
        {"name": "totals.bogus", "extracted": "1", "document": "2", "match": False},
    ])
    corrected, corrections = ai_verify.apply_corrections(d, verdict)
    assert corrections == []                 # nothing resolvable -> nothing applied
    assert corrected["capture"] == before["capture"]   # capture unchanged


def test_empty_or_unparseable_pdf_value_never_blanks():
    d = _draft()
    verdict = _verdict([
        {"name": "line[1].vat", "extracted": "162.75", "document": "", "match": False},
        {"name": "totals.gross", "extracted": "970.42", "document": "  ", "match": False},
        {"name": "invoice.due_date", "extracted": "2026-06-30", "document": "not-a-date",
         "match": False},
    ])
    corrected, corrections = ai_verify.apply_corrections(d, verdict)
    assert corrections == []                  # empty/unparseable -> skipped
    # original values preserved (never blanked)
    assert corrected["capture"]["lines"][0]["vat"] == 162.75
    assert corrected["capture"]["totals"]["gross_total"] == 970.42
    assert corrected["capture"]["header"]["invoice"]["due_date"] == "2026-06-30"


def test_matched_fields_are_not_touched():
    d = _draft()
    verdict = {"verdict": "discrepancies", "fields": [
        {"name": "line[1].vat", "extracted": "162.75", "document": "162.75", "match": True},
    ], "notes": ""}
    corrected, corrections = ai_verify.apply_corrections(d, verdict)
    assert corrections == []


def test_header_correction_updates_mapped_draft_key():
    d = _draft()
    verdict = _verdict([
        {"name": "supplier.name", "extracted": "DKV", "document": "DKV Mobility",
         "match": False},
        {"name": "invoice.due_date", "extracted": "2026-06-30", "document": "2026-07-01",
         "match": False},
    ])
    corrected, corrections = ai_verify.apply_corrections(d, verdict)
    assert corrected["capture"]["header"]["supplier"]["name"] == "DKV Mobility"
    assert corrected["supplier"] == "DKV Mobility"        # mapped draft key
    assert corrected["capture"]["header"]["invoice"]["due_date"] == "2026-07-01"


def test_no_capture_doc_returns_copy_no_corrections():
    d = {"supplier": "X", "lines": [{"net": 1.0, "vat": 0.2}]}
    verdict = _verdict([{"name": "line[1].net", "extracted": "1", "document": "2",
                         "match": False}])
    corrected, corrections = ai_verify.apply_corrections(d, verdict)
    assert corrections == []
    assert corrected == d and corrected is not d          # a copy, no corrections


def test_apply_corrections_never_raises_on_garbage():
    # malformed verdict / fields must not raise
    for bad in (None, {}, {"fields": "nope"}, {"fields": [None, 5, {"name": None}]}):
        corrected, corrections = ai_verify.apply_corrections(_draft(), bad)
        assert isinstance(corrections, list)


# --------------------------------------------------------------- web loop
def _tok(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_correct_route_persists_reverifies_and_renders(client, monkeypatch):
    import app as A, ai_verify as V, waiting_room as IQ, extract as EX
    monkeypatch.setattr(V, "enabled", lambda: True)
    monkeypatch.setattr(IQ, "get_job", lambda j: {"id": 1, "stored_path": "x",
                                                  "filename": "f.pdf", "status": "ready"})
    monkeypatch.setattr(IQ, "read_bytes", lambda p: b"%PDF fake")
    monkeypatch.setattr(EX, "unpack", lambda b, n: [("f.pdf", b"%PDF fake")])

    calls = {"n": 0}

    def _verify(pdf, draft, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            # first call: the discrepancy verdict the corrections come from
            return {"verdict": "discrepancies", "provider": "claude",
                    "model": "claude-opus-4-8", "pages": 2, "notes": "",
                    "fields": [{"name": "line[1].vat", "extracted": "162.75",
                                "document": "162.99", "match": False}]}
        # re-verify the corrected draft: now confirmed
        return {"verdict": "confirmed", "fields": [], "notes": "",
                "provider": "claude", "model": "claude-opus-4-8", "pages": 2}

    monkeypatch.setattr(V, "verify", _verify)

    A._stash_draft("ctok", _draft())
    r = client.post("/extract/ai-correct",
                    data={"_csrf": _tok(client, "/extract"), "token": "ctok",
                          "intake_job": "1", "period": "2026-05"})
    html = r.get_data(as_text=True)
    # corrections log + the green re-verified badge
    assert "AI corrections &amp; re-verify" in html or "AI corrections" in html
    assert "AI-corrected &amp; re-verified ✅" in html
    assert "line[1].vat" in html and "162.99" in html
    # the verify() was called twice (correct-from + re-verify)
    assert calls["n"] == 2
    # the corrected draft was persisted with the corrections + status
    d = A._load_draft("ctok")
    assert d["correction_status"] == "verified_after_correction"
    assert d["capture"]["lines"][0]["vat"] == 162.99
    assert d["lines"][0]["vat"] == 162.99
    assert any(c["field"] == "line[1].vat" for c in d["corrections"])
    # the human Confirm gate is still present and unaffected
    assert 'action="/extract/confirm"' in html


def test_correct_route_escapes_xss_in_corrected_value(client, monkeypatch):
    import app as A, ai_verify as V, waiting_room as IQ, extract as EX
    monkeypatch.setattr(V, "enabled", lambda: True)
    monkeypatch.setattr(IQ, "get_job", lambda j: {"id": 1, "stored_path": "x",
                                                  "filename": "f.pdf", "status": "ready"})
    monkeypatch.setattr(IQ, "read_bytes", lambda p: b"%PDF fake")
    monkeypatch.setattr(EX, "unpack", lambda b, n: [("f.pdf", b"%PDF fake")])
    xss = '<img src=x onerror=alert(1)>'

    def _verify(pdf, draft, **k):
        # a TEXT field (product) so the XSS survives coercion into the correction
        return {"verdict": "discrepancies", "provider": "claude", "model": "m",
                "pages": 1, "notes": "",
                "fields": [{"name": "line[1].product", "extracted": "Diesel",
                            "document": xss, "match": False}]}

    monkeypatch.setattr(V, "verify", _verify)
    A._stash_draft("xtok", _draft())
    r = client.post("/extract/ai-correct",
                    data={"_csrf": _tok(client, "/extract"), "token": "xtok",
                          "intake_job": "1", "period": "2026-05"})
    html = r.get_data(as_text=True)
    assert xss not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_correct_route_off_is_unavailable(client, monkeypatch):
    import app as A, ai_verify as V
    monkeypatch.setattr(V, "enabled", lambda: False)
    called = []
    monkeypatch.setattr(V, "verify", lambda *a, **k: called.append(1) or {})
    monkeypatch.setattr(V, "apply_corrections", lambda *a, **k: called.append("c") or (a[0], []))
    A._stash_draft("offc", _draft())
    r = client.post("/extract/ai-correct",
                    data={"_csrf": _tok(client, "/extract"), "token": "offc",
                          "intake_job": "1", "period": "2026-05"})
    assert called == []                       # OFF => no verify / no apply call
    html = r.get_data(as_text=True)
    assert "AI corrections &amp; re-verify" not in html   # the panel is absent
    # plain review form is rendered
    assert 'action="/extract/confirm"' in html


def test_correction_action_button_hidden_when_off(monkeypatch):
    import app as A, ai_verify as V
    monkeypatch.setattr(V, "enabled", lambda: False)
    assert A._ai_correction_action("tok", "1", "2026-05") == ""


def test_verify_panel_offers_correction_action_on_discrepancies(client, monkeypatch):
    import app as A, ai_verify as V
    monkeypatch.setattr(V, "enabled", lambda: True)
    monkeypatch.setattr(V, "provider_label", lambda *a, **k: "Claude (Anthropic)")
    result = {"verdict": "discrepancies", "provider": "claude", "model": "m", "pages": 1,
              "notes": "", "fields": [{"name": "line[1].vat", "extracted": "1",
                                       "document": "2", "match": False}]}
    with A.app.test_request_context("/extract"):
        html = A._ai_verify_panel(result, token="tok", intake_job="1", period="2026-05")
    assert 'action="/extract/ai-correct"' in html
    assert "Apply AI corrections" in html


def test_no_figure_committed_by_correct_route(client, monkeypatch):
    """The correct/verify routes edit the PRE-commit draft only — they must NOT register a
    statement. Guard: vat_refund.register_statement is never called by /extract/ai-correct."""
    import app as A, ai_verify as V, waiting_room as IQ, extract as EX, vat_refund as VR
    monkeypatch.setattr(V, "enabled", lambda: True)
    monkeypatch.setattr(IQ, "get_job", lambda j: {"id": 1, "stored_path": "x",
                                                  "filename": "f.pdf", "status": "ready"})
    monkeypatch.setattr(IQ, "read_bytes", lambda p: b"%PDF fake")
    monkeypatch.setattr(EX, "unpack", lambda b, n: [("f.pdf", b"%PDF fake")])
    monkeypatch.setattr(V, "verify", lambda pdf, draft, **k: {
        "verdict": "confirmed", "fields": [], "notes": "", "provider": "claude",
        "model": "m", "pages": 1})
    registered = []
    if hasattr(VR, "register_statement"):
        monkeypatch.setattr(VR, "register_statement",
                            lambda *a, **k: registered.append(1))
    A._stash_draft("nc", _draft())
    client.post("/extract/ai-correct",
                data={"_csrf": _tok(client, "/extract"), "token": "nc",
                      "intake_job": "1", "period": "2026-05"})
    assert registered == []
