"""
AI REVIEW ASSISTANT (advisory) — payload minimization/redaction, parsing, and the
hard invariants: no PDF/secret can reach the model, default backend 'none' makes ZERO
network calls, and review() never mutates the input draft. NO live API is called: the
per-backend `_call` is monkeypatched to canned JSON.
"""
import copy

import pytest

import ai_review


DRAFT = {
    "supplier": "DKV", "supplier_vat": "LV40003XXXX", "statement_ref": "S-001",
    "statement_date": "2026-05-31", "currency": "EUR", "customer": "SIA Test",
    "lines": [
        {"invoice_no": "BE001", "date": "2026-05-31", "country": "Belgium",
         "net": 1000.0, "vat": 210.0, "_source": "doc.pdf"},
        {"invoice_no": "DE001", "date": "2026-05-31", "country": "Germany",
         "net": 500.0, "vat": 95.0, "_source": "doc.pdf"},
    ],
    # private + secret material that must NEVER reach the model
    "_pdf_bytes": [("doc.pdf", b"%PDF-1.7 ...")],
    "pdf_text": "raw invoice text",
}

# supplier context carries bank/secret fields that must be redacted
CONTEXT = {
    "supplier": {"expected_name": "DKV Euro Service", "expected_vat": "LV40003XXXX",
                 "aliases": ["DKV", "DKV MOBILITY"],
                 "iban": "LV80BANK0000435195001", "swift": "HABALV22",
                 "beneficiary": "DKV Euro Service GmbH"},
    "customer": {"name": "SIA Test", "discount": 0.02, "ceiling": 100000,
                 "contact_email": "ops@example.com", "bank_iban": "LV12..."},
    "price_samples": [1.51, 1.55, 1.58, 1.60, 1.62],
    "expenditure_codes": {"BE001": 1, "DE001": 1},
}


def _build():
    return ai_review.build_payload(DRAFT, CONTEXT)


def test_payload_has_no_pdf_no_underscore_keys():
    p = _build()
    flat = repr(p)
    # the raw PDF bytes are gone, and so is the private '_pdf_bytes' key entirely
    assert "_pdf_bytes" not in p
    assert "%PDF" not in flat and b"%PDF".decode() not in flat
    # no key anywhere starts with '_'
    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                assert not str(k).startswith("_"), f"leaked private key {k}"
                keys(v)
        elif isinstance(o, list):
            for v in o:
                keys(v)
    keys(p)
    # include_text default False -> no pdf_text attached
    assert "pdf_text" not in p


def test_secrets_redacted_unless_in_needs():
    p = _build()
    flat = repr(p)
    for secret in ("LV80BANK0000435195001", "HABALV22", "DKV Euro Service GmbH",
                   "ops@example.com"):
        assert secret not in flat, f"secret {secret!r} leaked into payload"
    # iban present ONLY when explicitly allow-listed in needs
    p2 = ai_review.build_payload(DRAFT, CONTEXT, needs=("iban",))
    assert "LV80BANK0000435195001" in repr(p2)


def test_deterministic_findings_populated():
    p = _build()
    det = p["deterministic_findings"]
    assert det is not None and "lines" in det and "can_commit" in det
    assert len(det["lines"]) == 2


def test_parse_flags_drops_malformed_and_coerces_severity():
    raw = {"flags": [
        {"field": "supplier", "severity": "warn", "message": "alias mismatch",
         "suggestion": "use DKV"},
        {"field": "vat_id", "severity": "screaming", "message": "implausible"},  # bad sev
        {"field": "", "message": "no field"},                                    # dropped
        {"severity": "info", "message": "no field key"},                          # dropped
        {"field": "x"},                                                            # no msg
        "not a dict",                                                              # dropped
    ], "note": "looks broadly fine; verify Germany rate"}
    flags, note = ai_review.parse_flags(raw)
    assert len(flags) == 2
    assert flags[0]["severity"] == "warn"
    assert flags[1]["severity"] == "info"          # coerced
    assert flags[1]["suggestion"] is None
    assert note == "looks broadly fine; verify Germany rate"


def test_parse_flags_never_raises_on_junk():
    assert ai_review.parse_flags(None) == ([], None)
    assert ai_review.parse_flags("garbage") == ([], None)
    assert ai_review.parse_flags(123) == ([], None)


def test_backend_none_makes_zero_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_review, "_call",
                        lambda *a, **k: calls.append(1) or {"flags": [], "note": None})
    out = ai_review.review(DRAFT, CONTEXT, backend="none")
    assert calls == []                              # NO network call
    assert out["flags"] == [] and out["backend"] == "none"
    assert out["deterministic"]["lines"]            # deterministic block present


def test_review_returns_flags_and_note_with_mock_backend(monkeypatch):
    canned = {"flags": [{"field": "supplier", "severity": "warn",
                         "message": "name vs alias mismatch", "suggestion": None}],
              "note": "Prices in NET EUR/L look in range."}
    monkeypatch.setattr(ai_review, "_call", lambda *a, **k: canned)
    # avoid any real data-lake write during the test
    import data_lake
    monkeypatch.setattr(data_lake, "put", lambda *a, **k: "stub")
    out = ai_review.review(DRAFT, CONTEXT, backend="claude")
    assert out["backend"] == "claude"
    assert len(out["flags"]) == 1 and out["flags"][0]["severity"] == "warn"
    assert out["note"] == "Prices in NET EUR/L look in range."
    assert out["deterministic"]["lines"]
    # archived KEYS only — never secret values
    assert "supplier_context" in out["sent_keys"]


def test_review_does_not_mutate_draft(monkeypatch):
    canned = {"flags": [], "note": None}
    monkeypatch.setattr(ai_review, "_call", lambda *a, **k: canned)
    import data_lake
    monkeypatch.setattr(data_lake, "put", lambda *a, **k: "stub")
    before = copy.deepcopy(DRAFT)
    ai_review.review(DRAFT, CONTEXT, backend="claude")
    assert DRAFT == before, "review() mutated the input draft"
    # and the 'none' path too
    ai_review.review(DRAFT, CONTEXT, backend="none")
    assert DRAFT == before
