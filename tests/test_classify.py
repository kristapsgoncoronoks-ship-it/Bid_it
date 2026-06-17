"""
DATA CLASSIFICATION / DLP (classify.py) — an APP-OWNED, ADVISORY overlay that scans a
document's TEXT for sensitive data, assigns an ordered sensitivity LABEL, surfaces it, and
(OPT-IN) gates what may be sent to the EXTERNAL AI by sensitivity.

Load-bearing invariants asserted here:
  * scan_text detects a planted IBAN / email / phone and assigns the right label;
  * the stored finding carries ONLY {type, count} — the raw IBAN/email VALUE is NEVER
    stored or returned (asserted absent from the persisted record);
  * the label ordering (public < internal < confidential < restricted);
  * external_ai_allowed: True under the DEFAULT permissive policy, False when the policy is
    tightened below the doc's label;
  * the external-AI entry points (ai_verify.verify / vision_capture.capture) are BLOCKED
    when over the limit — NO provider call (the mocked backend is uncalled) + a visible note
    + an admin error-log entry — and behave byte-identically when permissive / under it;
  * a scan error FAILS OPEN (does not block);
  * the badge renders + ESCAPES an XSS finding type;
  * classify.db is a SEPARATE app-owned DB (no product-DB write).
"""
import os
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import classify  # noqa: E402


# A valid IBAN (mod-97 passes), a BIC, an email, a phone, a Luhn-valid card.
IBAN = "DE89370400440532013000"
BIC = "COBADEFFXXX"
EMAIL = "john.doe@example.com"
PHONE = "+49 30 1234567"
CARD = "4111 1111 1111 1111"   # Luhn-valid test Visa


@pytest.fixture()
def cl(tmp_path, monkeypatch):
    """Repoint classify.DB at a temp file (so we never touch the live classify.db)."""
    monkeypatch.setattr(classify, "DB", str(tmp_path / "classify.db"), raising=True)
    monkeypatch.setattr(classify, "_SCHEMA_READY", set(), raising=True)
    return classify


# ----------------------------------------------------------------- detection + labels
def test_scan_detects_iban_email_phone(cl):
    text = f"Pay to IBAN {IBAN}. Contact {EMAIL} or {PHONE}."
    r = cl.scan_text(text)
    types = {f["type"] for f in r["findings"]}
    assert "iban" in types
    assert "email" in types
    assert "phone" in types
    # IBAN drives the label to the most sensitive 'restricted'
    assert r["label"] == "restricted"


def test_iban_label_restricted(cl):
    assert cl.scan_text(f"acc {IBAN}")["label"] == "restricted"


def test_card_label_restricted(cl):
    r = cl.scan_text(f"card {CARD}")
    assert any(f["type"] == "credit_card" for f in r["findings"])
    assert r["label"] == "restricted"


def test_email_only_is_confidential(cl):
    r = cl.scan_text(f"reach us at {EMAIL}")
    assert {f["type"] for f in r["findings"]} == {"email"}
    assert r["label"] == "confidential"


def test_personal_name_is_internal(cl):
    r = cl.scan_text("Signed by John Smith on the form.")
    assert any(f["type"] == "personal_name" for f in r["findings"])
    assert r["label"] == "internal"


def test_plain_text_is_public(cl):
    assert cl.scan_text("DIESEL 1000 LITRES TOTAL 1500 EUR")["label"] == "public"
    assert cl.scan_text("")["label"] == "public"


def test_label_ordering():
    assert classify.rank("public") < classify.rank("internal") \
        < classify.rank("confidential") < classify.rank("restricted")
    assert classify.label_at_least("restricted", "confidential")
    assert not classify.label_at_least("internal", "restricted")


def test_invalid_iban_not_flagged(cl):
    # a country-prefixed alphanumeric that FAILS mod-97 is not an IBAN
    r = cl.scan_text("ref DE00000000000000000000")
    assert not any(f["type"] == "iban" for f in r["findings"])


# ----------------------------------------------------------------- never stores the value
def test_persisted_record_never_contains_the_iban_value(cl):
    text = f"Pay to IBAN {IBAN}. Email {EMAIL}."
    rec_result = cl.classify_document("doc:1", text)
    assert rec_result["label"] == "restricted"

    stored = cl.classification("doc:1")
    assert stored is not None
    assert stored["label"] == "restricted"
    # findings carry ONLY {type, count}
    for f in stored["findings"]:
        assert set(f.keys()) == {"type", "count"}
    # the RAW IBAN / email value is NEVER present anywhere in the stored record
    blob = repr(stored)
    assert IBAN not in blob
    assert EMAIL not in blob
    # nor in the returned scan result
    assert IBAN not in repr(rec_result)
    assert EMAIL not in repr(rec_result)


def test_db_row_has_no_raw_value(cl):
    cl.classify_document("doc:9", f"IBAN {IBAN}")
    con = cl.connect()
    try:
        rows = con.execute("SELECT subject_ref, label, findings FROM classifications").fetchall()
    finally:
        con.close()
    assert rows
    for r in rows:
        assert IBAN not in (r["findings"] or "")
        assert IBAN not in (r["label"] or "")


# ----------------------------------------------------------------- the OPT-IN gate
def _patch_policy(monkeypatch, value):
    import auth
    monkeypatch.setattr(auth, "get_setting",
                        lambda k, d=None: value if k == classify.POLICY_SETTING else d)


def test_external_ai_allowed_default_permissive(cl, monkeypatch):
    # default policy = restricted = permissive => a restricted doc is allowed
    _patch_policy(monkeypatch, None)
    cl.classify_document("doc:r", f"IBAN {IBAN}")
    allowed, info = cl.external_ai_allowed("doc:r")
    assert allowed is True
    assert info["max"] == classify.DEFAULT_MAX_SENSITIVITY


def test_external_ai_blocked_when_tightened(cl, monkeypatch):
    _patch_policy(monkeypatch, "confidential")     # tighten below 'restricted'
    cl.classify_document("doc:r", f"IBAN {IBAN}")
    allowed, info = cl.external_ai_allowed("doc:r")
    assert allowed is False
    assert info["blocked"] is True
    assert "restricted" in info["reason"] and "confidential" in info["reason"]


def test_external_ai_allowed_at_or_below_limit(cl, monkeypatch):
    _patch_policy(monkeypatch, "confidential")
    cl.classify_document("doc:e", f"email {EMAIL}")   # confidential == limit
    allowed, _ = cl.external_ai_allowed("doc:e")
    assert allowed is True


def test_gate_fails_open_when_never_classified(cl, monkeypatch):
    _patch_policy(monkeypatch, "internal")
    allowed, info = cl.external_ai_allowed("doc:never")
    assert allowed is True                          # unclassified -> fail OPEN
    assert info["label"] is None


def test_gate_fails_open_on_scan_error(cl, monkeypatch):
    _patch_policy(monkeypatch, "public")
    # make classification() raise -> external_ai_allowed must FAIL OPEN (allow)
    def _boom(ref):
        raise RuntimeError("classifier exploded")
    monkeypatch.setattr(cl, "classification", _boom)
    allowed, _ = cl.external_ai_allowed("doc:x")
    assert allowed is True


def test_scan_error_returns_public(cl, monkeypatch):
    # a detector blowing up must not raise; scan_text returns the safe empty result
    monkeypatch.setattr(cl, "_IBAN_RE", None)       # .finditer on None -> AttributeError
    r = cl.scan_text(f"IBAN {IBAN}")
    assert r == {"label": "public", "findings": []}


# ----------------------------------------------------------------- entry-point blocking
def test_ai_verify_blocked_no_provider_call(cl, monkeypatch):
    """ai_verify.verify with a subject_ref over the limit must REFUSE: no provider call,
    a visible note, an admin error-log entry, and a 'blocked' verdict."""
    import ai_verify
    import auth
    _patch_policy(monkeypatch, "confidential")
    cl.classify_document("doc:block", f"IBAN {IBAN}")   # restricted > confidential

    called = []
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", lambda *a, **k: called.append(1))
    monkeypatch.setitem(ai_verify._VISION_CALL, "openai", lambda *a, **k: called.append(1))
    # even a configured provider must not be reached
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "claude")
    logged = []
    monkeypatch.setattr(auth, "log_error",
                        lambda *a, **k: logged.append((a, k)))

    out = ai_verify.verify(b"%PDF fake", {"supplier": "X", "lines": []},
                           subject_ref="doc:block")
    assert out["verdict"] == "blocked"
    assert out.get("dlp_blocked") is True
    assert "blocked by DLP policy" in out["notes"]
    assert called == []                             # NEVER a provider call
    assert logged                                   # surfaced to the admin error log


def test_ai_verify_byte_identical_when_permissive(cl, monkeypatch):
    """Under the default permissive policy the gate is inert — verify() proceeds to the
    provider exactly as before."""
    import ai_verify
    _patch_policy(monkeypatch, None)
    cl.classify_document("doc:ok", f"IBAN {IBAN}")

    class _FakeImg:
        def save(self, buf, format=None):
            buf.write(b"PNG")
    monkeypatch.setattr(ai_verify, "_render_pages", lambda b, c: [b"PNG"])
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "claude")
    monkeypatch.setattr(ai_verify, "model_name", lambda be: "m")
    called = []

    def _call(prompt, data_str, images, model=None):
        called.append(1)
        return {"verdict": "confirmed", "fields": [], "notes": "ok"}
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", _call)

    out = ai_verify.verify(b"%PDF fake", {"supplier": "X", "lines": []},
                           subject_ref="doc:ok")
    assert out["verdict"] == "confirmed"
    assert called == [1]                            # provider WAS reached (unchanged)


def test_ai_verify_no_subject_ref_unchanged(cl, monkeypatch):
    """Without a subject_ref the gate never runs (backward compatible)."""
    import ai_verify
    monkeypatch.setattr(ai_verify, "_render_pages", lambda b, c: [b"PNG"])
    monkeypatch.setattr(ai_verify, "_provider", lambda *a, **k: "claude")
    monkeypatch.setattr(ai_verify, "model_name", lambda be: "m")
    called = []
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda *a, **k: called.append(1) or
                        {"verdict": "confirmed", "fields": [], "notes": ""})
    out = ai_verify.verify(b"%PDF fake", {"supplier": "X", "lines": []})
    assert out["verdict"] == "confirmed"
    assert called == [1]


def test_vision_capture_blocked_no_provider_call(cl, monkeypatch):
    """vision_capture.capture with a subject_ref over the limit must REFUSE: no provider
    call, returns None (fallback), and records a visible reason."""
    import vision_capture as VC
    import ai_verify
    _patch_policy(monkeypatch, "internal")
    cl.classify_document("doc:vc", f"email {EMAIL}")   # confidential > internal

    called = []
    monkeypatch.setattr(ai_verify, "_render_pages",
                        lambda b, c: called.append("render") or [b"PNG"])
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", lambda *a, **k: called.append(1))
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: "claude")

    out = VC.capture(b"%PDF fake", subject_ref="doc:vc")
    assert out is None                              # blocked -> fallback
    assert called == []                             # NEVER rendered or called the provider
    reason = VC.take_last_error()
    assert reason and "blocked by DLP policy" in reason


def test_vision_capture_byte_identical_when_under_limit(cl, monkeypatch):
    import vision_capture as VC
    import ai_verify
    _patch_policy(monkeypatch, "restricted")        # permissive
    cl.classify_document("doc:vc2", f"email {EMAIL}")

    monkeypatch.setattr(ai_verify, "_render_pages", lambda b, c: [b"PNG"])
    monkeypatch.setattr(VC, "_provider", lambda *a, **k: "claude")
    called = []

    def _call(prompt, data_str, images):
        called.append(1)
        return {"header": {}, "lines": [], "totals": {}}
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", _call)

    out = VC.capture(b"%PDF fake", subject_ref="doc:vc2")
    assert out is not None
    assert called == [1]                            # provider reached (unchanged)


# ----------------------------------------------------------------- badge rendering + escaping
def test_badge_escapes_xss(monkeypatch):
    import app
    rec = {"label": "restricted",
           "findings": [{"type": "<script>alert(1)</script>", "count": 2}]}
    html = app._classification_badge(rec)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "Restricted" in html


def test_badge_empty_for_none():
    import app
    assert app._classification_badge(None) == ""


# ----------------------------------------------------------------- app-owned (no product DB)
def test_classify_db_is_separate_app_owned(cl):
    cl.classify_document("doc:own", f"IBAN {IBAN}")
    assert os.path.exists(cl.DB)
    assert cl.DB.endswith("classify.db")
    # never the engine product DBs
    for product in ("fuel_history.db", "suppliers.db", "vat_claims.db"):
        assert not cl.DB.endswith(product)


def test_counts_by_label(cl, monkeypatch):
    cl.classify_document("doc:a", f"IBAN {IBAN}")       # restricted
    cl.classify_document("doc:b", f"email {EMAIL}")     # confidential
    cl.classify_document("doc:c", "plain text")         # public
    counts = cl.counts_by_label()
    assert counts.get("restricted") == 1
    assert counts.get("confidential") == 1
    assert counts.get("public") == 1
