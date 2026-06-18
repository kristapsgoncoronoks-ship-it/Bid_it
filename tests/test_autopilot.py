"""
Tests for the AUTO-PILOT INTAKE gate (autopilot.py).

Covers the PURE evaluate() gate (pass + every conservative reject reason), the autofile()
registration/validation path (enqueues on a good draft, refuses on a validation failure),
and the default-OFF enabled() setting.
"""
import os
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import autopilot


def _good_draft():
    """A clean, high-confidence, fileable draft."""
    return {
        "supplier": "OMV", "statement_ref": "STMT-2026-05", "confidence": "high",
        "statement_date": "2026-05-31", "customer": "Acme UAB",
        "lines": [
            {"invoice_no": "INV001", "date": "2026-05-10", "country": "LT",
             "currency": "EUR", "net": 100.00, "vat": 21.00},
            {"invoice_no": "INV002", "date": "2026-05-11", "country": "LT",
             "currency": "EUR", "net": 50.00, "vat": 10.50},
        ],
    }


# ---------------------------------------------------------------- evaluate()
def test_evaluate_passes_on_clean_high_confidence_draft():
    ev = autopilot.evaluate(_good_draft())
    assert ev["ok"] is True
    assert ev["reasons"] == []


def test_evaluate_passes_with_verify_ok_true():
    ev = autopilot.evaluate(_good_draft(), verify_ok=True)
    assert ev["ok"] is True


@pytest.mark.parametrize("conf", ["medium", "low", None])
def test_evaluate_fails_on_non_high_confidence(conf):
    d = _good_draft()
    d["confidence"] = conf
    ev = autopilot.evaluate(d)
    assert ev["ok"] is False
    assert any("confidence" in r for r in ev["reasons"])


def test_evaluate_fails_on_missing_supplier():
    d = _good_draft()
    d["supplier"] = ""
    ev = autopilot.evaluate(d)
    assert ev["ok"] is False
    assert any("supplier" in r for r in ev["reasons"])


def test_evaluate_fails_on_missing_statement_ref():
    d = _good_draft()
    d["statement_ref"] = ""
    ev = autopilot.evaluate(d)
    assert ev["ok"] is False
    assert any("statement_ref" in r for r in ev["reasons"])


def test_evaluate_fails_on_no_lines():
    d = _good_draft()
    d["lines"] = []
    ev = autopilot.evaluate(d)
    assert ev["ok"] is False
    assert any("no lines" in r for r in ev["reasons"])


def test_evaluate_fails_on_no_period():
    d = _good_draft()
    d["statement_date"] = ""
    for ln in d["lines"]:
        ln["date"] = ""
    ev = autopilot.evaluate(d)
    assert ev["ok"] is False
    assert any("period" in r for r in ev["reasons"])


def test_evaluate_passes_period_from_line_when_no_statement_date():
    d = _good_draft()
    d["statement_date"] = ""   # no header date; line dates still carry YYYY-MM
    ev = autopilot.evaluate(d)
    assert ev["ok"] is True


@pytest.mark.parametrize("bad_inv", ["UNMATCHED", "INPUT", "ALL:", "ALL:FUEL", ""])
def test_evaluate_fails_on_synthetic_line(bad_inv):
    d = _good_draft()
    d["lines"][1]["invoice_no"] = bad_inv
    ev = autopilot.evaluate(d)
    assert ev["ok"] is False
    assert any("synthetic" in r for r in ev["reasons"])


def test_evaluate_fails_on_verify_ok_false():
    ev = autopilot.evaluate(_good_draft(), verify_ok=False)
    assert ev["ok"] is False
    assert any("verification" in r for r in ev["reasons"])


# ---------------------------------------------------------------- autofile()
def test_autofile_good_draft_enqueues_registration(monkeypatch, admin_session):
    import waiting_room as IQ
    captured = {}

    def fake_enqueue(payload, user="system"):
        captured["payload"] = payload
        captured["user"] = user
        return 4242, "queued"

    monkeypatch.setattr(IQ, "enqueue_registration", fake_enqueue)
    # save_baseline writes to a runtime DB; stub it so the test leaves no churn.
    import validate as VAL
    monkeypatch.setattr(VAL, "save_baseline", lambda *a, **k: None)

    d = _good_draft()
    d["_pdf_bytes"] = []   # no PDFs -> vault step is a no-op
    st, info = autopilot.autofile(None, None, d, actor="autopilot")

    assert st == "done"
    assert info["filed"] is True
    assert info["period"] == "2026-05"
    assert info["lines"] == 2
    assert captured["user"] == "autopilot"
    pl = captured["payload"]
    assert pl["supplier"] == "OMV"
    assert pl["statement_ref"] == "STMT-2026-05"
    assert pl["period"] == "2026-05"
    assert pl["customer"] == "Acme UAB"
    # lines carried as TUPLES (inv, date, country, ccy, net, vat)
    assert isinstance(pl["lines"][0], tuple)
    assert pl["lines"][0][0] == "INV001"
    assert "autopilot" in pl["notes"]


def test_autofile_validation_failure_does_not_enqueue(monkeypatch, admin_session):
    import waiting_room as IQ
    import validate as VAL
    called = {"n": 0}

    def fake_enqueue(payload, user="system"):
        called["n"] += 1
        return 1, "queued"

    monkeypatch.setattr(IQ, "enqueue_registration", fake_enqueue)
    monkeypatch.setattr(VAL, "save_baseline", lambda *a, **k: None)
    # Force the deterministic gate to REFUSE — must NOT enqueue and must leave 'ready'.
    monkeypatch.setattr(VAL, "validate_batch",
                        lambda lines, **k: {"can_commit": False, "errors": 3, "warnings": 0,
                                            "lines": [], "gross": 0, "tie": None})

    d = _good_draft()
    d["_pdf_bytes"] = []
    st, info = autopilot.autofile(None, None, d, actor="autopilot")

    assert st == "ready"
    assert info["filed"] is False
    assert "validation" in info["reason"]
    assert called["n"] == 0


def test_autofile_no_period_does_not_enqueue(monkeypatch, admin_session):
    import waiting_room as IQ
    import validate as VAL
    called = {"n": 0}
    monkeypatch.setattr(IQ, "enqueue_registration",
                        lambda payload, user="system": (called.__setitem__("n", called["n"] + 1), 1)[1])
    monkeypatch.setattr(VAL, "save_baseline", lambda *a, **k: None)
    # validation passes, but no derivable period -> still must not file.
    monkeypatch.setattr(VAL, "validate_batch",
                        lambda lines, **k: {"can_commit": True, "errors": 0, "warnings": 0,
                                            "lines": [], "gross": 0, "tie": None})
    d = _good_draft()
    d["statement_date"] = ""
    for ln in d["lines"]:
        ln["date"] = ""
    d["_pdf_bytes"] = []
    st, info = autopilot.autofile(None, None, d, actor="autopilot")
    assert st == "ready"
    assert info["filed"] is False
    assert info["reason"] == "no period"
    assert called["n"] == 0


# ---------------------------------------------------------------- enabled()
def test_enabled_default_false(admin_session):
    import auth
    auth.set_setting("intake_autopilot_enabled", "off")
    assert autopilot.enabled() is False


def test_enabled_true_when_on(admin_session):
    import auth
    auth.set_setting("intake_autopilot_enabled", "on")
    try:
        assert autopilot.enabled() is True
    finally:
        auth.set_setting("intake_autopilot_enabled", "off")
