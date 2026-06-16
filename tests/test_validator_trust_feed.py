"""A4 — the confidence/trust model learns from the DETERMINISTIC validator, not just the
advisory AI review, and the review form surfaces per-line provenance.

`_feed_validator_trust` records a per-country clean/flagged signal (source='validator')
from a validate_batch result; `_provenance_badge` flags AI-derived lines for scrutiny.
Trust still governs nothing but whether the advisory AI panel may be skipped."""
import pytest


@pytest.fixture()
def conf_tmp(tmp_path, monkeypatch):
    import confidence
    monkeypatch.setattr(confidence, "DB", str(tmp_path / "confidence.db"))
    return confidence


def _vr(*country_verdicts):
    """Build a minimal validate_batch-shaped result from (country, verdict) pairs."""
    return {"lines": [{"line": {"country": c}, "verdict": v} for c, v in country_verdicts]}


def test_clean_country_grows_trust(conf_tmp):
    import app
    before = conf_tmp.trust("DKV", "Belgium")
    app._feed_validator_trust("DKV", _vr(("Belgium", "ok"), ("Belgium", "warn")))
    after = conf_tmp.trust("DKV", "Belgium")
    assert after > before                                  # warn still counts clean (no error)
    ev = conf_tmp.recent_events(10)
    assert ev and ev[0]["source"] == "validator" and ev[0]["clean"] is True


def test_error_country_decays_trust(conf_tmp):
    import app
    # seed some trust, then an error line must flag it down
    conf_tmp.record_validation("DKV", "Germany", clean=True)
    before = conf_tmp.trust("DKV", "Germany")
    app._feed_validator_trust("DKV", _vr(("Germany", "error")))
    after = conf_tmp.trust("DKV", "Germany")
    assert after < before
    assert conf_tmp.recent_events(1)[0]["clean"] is False


def test_per_country_split_in_one_batch(conf_tmp):
    import app
    app._feed_validator_trust("DKV", _vr(("Belgium", "ok"), ("Germany", "error")))
    sb = {(r["supplier"], r["country"]): r for r in conf_tmp.scoreboard()}
    assert sb[("DKV", "Belgium")]["n_clean"] == 1 and sb[("DKV", "Belgium")]["n_flagged"] == 0
    assert sb[("DKV", "Germany")]["n_flagged"] == 1 and sb[("DKV", "Germany")]["n_clean"] == 0


def test_feed_never_raises_on_bad_input(conf_tmp):
    import app
    app._feed_validator_trust("DKV", {})                   # no lines key path
    app._feed_validator_trust("DKV", {"lines": [{"line": {}, "verdict": "ok"}]})


# ---------------------------------------------------------------- provenance badge
def test_provenance_badge_flags_ai_and_structured():
    import app
    assert "AI" in app._provenance_badge("ai") and "bad" in app._provenance_badge("ai")
    assert "structured" in app._provenance_badge("e-invoice")
    assert "ok" in app._provenance_badge("e-invoice")
    assert "parse error" in app._provenance_badge("parse error: boom")
    assert "—" in app._provenance_badge("")
    # a filename/parser source passes through, escaped
    assert "BE-cover.pdf" in app._provenance_badge("BE-cover.pdf")


# ---------------------------------------------------------------- capture findings on review
def test_capture_findings_html_surfaces_problems():
    import app
    draft = {"supplier": "DKV", "supplier_vat": "DE12345",      # malformed for DE
             "lines": [{"invoice_no": "A1", "net": 100, "vat": 19},
                       {"invoice_no": "A1", "net": 100, "vat": 19}]}   # in-batch dup
    html = app._capture_findings_html(draft)
    assert "Capture checks" in html
    assert "VAT-ID" in html or "vat" in html.lower()
    assert "duplicate" in html.lower() or "more than once" in html.lower()


def test_capture_findings_html_empty_when_clean():
    import app
    draft = {"supplier": "DKV", "supplier_vat": "DE811569869",
             "lines": [{"invoice_no": "A1", "net": 100, "vat": 19}]}
    assert app._capture_findings_html(draft) == ""
