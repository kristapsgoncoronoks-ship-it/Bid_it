"""
CAPTURE-CONFIDENCE / learning loop — per-(supplier × field) capture-accuracy ledger.

CARDINAL RULE under test: ADVISORY ONLY. The module records how often a supplier's field
was CAPTURED WRONG (from the AI-correction signal + the human-edit-at-confirm signal) so
the review screen can flag mis-read fields — it NEVER gates a legal/VAT check and NEVER
mutates a captured figure. Every public function NEVER raises; aggregation is smoothed so a
field with too few samples is never flagged.

Determinism: each test owns a throwaway capture_confidence.db (the module DB path is
monkeypatched to a tmp file) so there is no cross-test bleed.
"""
import re

import pytest


# ---------------------------------------------------------------- isolation
@pytest.fixture()
def cc(tmp_path, monkeypatch):
    """capture_confidence module pointed at a throwaway DB (no shared state, no churn).
    Audit is best-effort and tolerated-absent, but stub auth.connect to avoid touching a
    real security.db from a unit test."""
    import capture_confidence
    monkeypatch.setattr(capture_confidence, "DB", str(tmp_path / "capture_confidence.db"))
    monkeypatch.setattr(capture_confidence, "_auth_connect", lambda: None)
    return capture_confidence


# ---------------------------------------------------------------- aggregation
def test_record_outcome_aggregates_per_field(cc):
    cc.record_outcome("DKV", "line.vat", was_correct=True)
    cc.record_outcome("DKV", "line.vat", was_correct=True)
    cc.record_outcome("DKV", "line.vat", was_correct=False)
    fa = cc.field_accuracy("DKV", "line.vat")
    assert fa["n"] == 3
    assert fa["rate"] == pytest.approx(2 / 3)
    # a different field is a separate bucket
    cc.record_outcome("DKV", "line.net", was_correct=True)
    assert cc.field_accuracy("DKV", "line.net") == {"rate": 1.0, "n": 1}
    # a different supplier is a separate bucket
    assert cc.field_accuracy("UTA", "line.vat") == {"rate": None, "n": 0}


def test_supplier_accuracy_overall(cc):
    cc.record_outcome("DKV", "line.vat", was_correct=False)   # 0/1
    cc.record_outcome("DKV", "line.net", was_correct=True)    # 1/1
    cc.record_outcome("DKV", "line.net", was_correct=True)    # 2/2
    sa = cc.supplier_accuracy("DKV")
    assert sa["n"] == 3
    assert sa["rate"] == pytest.approx(2 / 3)
    assert cc.supplier_accuracy("NOBODY") == {"rate": None, "n": 0}


def test_unseen_field_is_none(cc):
    assert cc.field_accuracy("X", "line.vat") == {"rate": None, "n": 0}


# ---------------------------------------------------------------- smoothing
def test_weak_fields_smoothed_below_min_samples(cc):
    # two misses on the same field -> 0% but only n=2 (< MIN_SAMPLES) -> NOT flagged
    cc.record_outcome("DKV", "line.vat", was_correct=False)
    cc.record_outcome("DKV", "line.vat", was_correct=False)
    assert cc.weak_fields("DKV") == []
    # a third miss -> n=3, 0% -> now flagged
    cc.record_outcome("DKV", "line.vat", was_correct=False)
    weak = cc.weak_fields("DKV")
    assert [w["field"] for w in weak] == ["line.vat"]
    assert weak[0]["rate"] == pytest.approx(0.0)
    assert weak[0]["n"] == 3


def test_weak_fields_only_low_accuracy(cc):
    # a well-captured field (>= threshold) is NOT weak even with plenty of samples
    for _ in range(10):
        cc.record_outcome("DKV", "line.net", was_correct=True)
    # a poorly-captured field IS weak
    for _ in range(4):
        cc.record_outcome("DKV", "line.vat", was_correct=False)
    fields = [w["field"] for w in cc.weak_fields("DKV")]
    assert fields == ["line.vat"]


def test_weak_fields_threshold_boundary(cc):
    # 4/5 = 0.80 == WEAK_THRESHOLD -> strictly-below means NOT weak
    cc.record_outcome("DKV", "line.vat", was_correct=False)
    for _ in range(4):
        cc.record_outcome("DKV", "line.vat", was_correct=True)
    assert cc.field_accuracy("DKV", "line.vat")["rate"] == pytest.approx(0.80)
    assert cc.weak_fields("DKV") == []
    # 3/5 = 0.60 < 0.80 -> weak
    for _ in range(2):
        cc.record_outcome("UTA", "line.vat", was_correct=False)
    for _ in range(3):
        cc.record_outcome("UTA", "line.vat", was_correct=True)
    assert [w["field"] for w in cc.weak_fields("UTA")] == ["line.vat"]


# ---------------------------------------------------------------- never-raise
def test_public_functions_never_raise_on_broken_db(cc, monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(cc, "connect", boom)
    # all read/write paths swallow and return a safe answer
    assert cc.record_outcome("DKV", "line.vat", was_correct=True) == {"rate": None, "n": 0}
    assert cc.field_accuracy("DKV", "line.vat") == {"rate": None, "n": 0}
    assert cc.supplier_accuracy("DKV") == {"rate": None, "n": 0}
    assert cc.weak_fields("DKV") == []
    assert cc.scoreboard() == []


def test_record_outcome_ignores_empty_key(cc):
    assert cc.record_outcome("", "line.vat", was_correct=True) == {"rate": None, "n": 0}
    assert cc.record_outcome("DKV", "", was_correct=True) == {"rate": None, "n": 0}
    assert cc.scoreboard() == []


# ---------------------------------------------------------------- field-name mapping
def test_normalize_verify_field():
    n = __import__("capture_confidence").normalize_verify_field
    assert n("line[2].vat") == "line.vat"
    assert n("lines[0].net") == "line.net"
    assert n("invoice.due_date") == "invoice.due_date"
    assert n("header.supplier.name") == "supplier.name"
    assert n("totals.gross") == "totals.gross"
    assert n("") is None
    assert n(None) is None


# ---------------------------------------------------------------- AI-correction signal
def test_ai_correction_path_records_misses(cc, monkeypatch):
    """ai_verify.apply_corrections feeds the loop: each corrected field -> was_correct=False
    for (supplier, normalized field)."""
    import ai_verify
    monkeypatch.setattr(ai_verify, "capture_confidence", cc, raising=False)
    draft = {
        "supplier": "DKV",
        "capture": {"lines": [{"vat": 1.0, "net": 10.0}]},
        "lines": [{"vat": 1.0, "net": 10.0}],
    }
    verdict = {"fields": [
        {"name": "line[1].vat", "extracted": "1.00", "document": "2.10", "match": False},
        {"name": "line[1].net", "extracted": "10.00", "document": "10.00", "match": True},
    ]}
    corrected, corrections = ai_verify.apply_corrections(draft, verdict)
    # the corrected field was captured WRONG; the matched field is not recorded here
    assert cc.field_accuracy("DKV", "line.vat") == {"rate": 0.0, "n": 1}
    assert cc.field_accuracy("DKV", "line.net") == {"rate": None, "n": 0}
    # advisory: the INPUT draft is not mutated in place (apply_corrections works on a copy)
    assert draft["capture"]["lines"][0]["vat"] == 1.0


# ---------------------------------------------------------------- human-edit-at-confirm signal
def test_confirm_path_records_edits_and_unchanged(cc, monkeypatch, tmp_path):
    """The confirm path diffs the CAPTURED draft against the human-confirmed form: an edited
    field -> False, an unchanged field -> True."""
    import app
    monkeypatch.setattr(app, "_log_exc", lambda *a, **k: None)

    captured = {
        "supplier": "DKV",
        "statement_ref": "S-1",
        "lines": [{"invoice_no": "INV1", "net": 100.0, "vat": 21.0,
                   "country": "DE", "currency": "EUR", "date": "2026-01-01"}],
    }

    class _Form(dict):
        def get(self, k, default=""):
            return dict.get(self, k, default)

    form = _Form({
        "supplier": "DKV",              # unchanged
        "stmt_ref": "S-1",             # unchanged
        "inv_0": "INV1",               # unchanged
        "net_0": "100.0",              # unchanged (numeric value)
        "vat_0": "25.50",             # EDITED (21.0 -> 25.50)
        "ctry_0": "DE",                # unchanged
        "ccy_0": "EUR",                # unchanged
        "date_0": "2026-01-01",        # unchanged
    })

    class _Req:
        pass
    req = _Req()
    req.form = form
    monkeypatch.setattr(app, "request", req)

    app._feed_capture_confidence_confirm("DKV", captured)

    # the edited VAT field is a capture MISS
    assert cc.field_accuracy("DKV", "line.vat") == {"rate": 0.0, "n": 1}
    # the unchanged fields are correct captures
    assert cc.field_accuracy("DKV", "line.net") == {"rate": 1.0, "n": 1}
    assert cc.field_accuracy("DKV", "line.invoice_no") == {"rate": 1.0, "n": 1}
    assert cc.field_accuracy("DKV", "invoice.statement_ref") == {"rate": 1.0, "n": 1}


def test_confirm_path_numeric_reformat_is_not_an_edit(cc, monkeypatch):
    """A pure reformat (12.5 captured, '12.50' confirmed) is NOT a miss — compared by value."""
    import app
    monkeypatch.setattr(app, "_log_exc", lambda *a, **k: None)
    captured = {"supplier": "DKV",
                "lines": [{"vat": 12.5, "net": 10.0, "invoice_no": "I", "country": "DE",
                           "currency": "EUR", "date": "2026-01-01"}]}

    class _Form(dict):
        def get(self, k, default=""):
            return dict.get(self, k, default)
    form = _Form({"vat_0": "12.50", "net_0": "10", "inv_0": "I", "ctry_0": "DE",
                  "ccy_0": "EUR", "date_0": "2026-01-01"})

    class _Req:
        pass
    req = _Req(); req.form = form
    monkeypatch.setattr(app, "request", req)
    app._feed_capture_confidence_confirm("DKV", captured)
    assert cc.field_accuracy("DKV", "line.vat") == {"rate": 1.0, "n": 1}


# ---------------------------------------------------------------- tenancy
def test_tenant_stamped(cc):
    cc.record_outcome("DKV", "line.vat", was_correct=True)
    con = cc.connect()
    try:
        rows = con.execute("SELECT tenant_id FROM capture_accuracy").fetchall()
    finally:
        con.close()
    assert rows and all(r["tenant_id"] == "default" for r in rows)


# ---------------------------------------------------------------- review-screen hints
def test_review_screen_accuracy_line_and_weak_hints(cc, monkeypatch):
    import app
    monkeypatch.setattr(app, "_log_exc", lambda *a, **k: None)
    # build a weak vat field for DKV with enough samples
    for _ in range(4):
        cc.record_outcome("DKV", "line.vat", was_correct=False)
    for _ in range(4):
        cc.record_outcome("DKV", "line.net", was_correct=True)
    line, weak = app._capture_accuracy_hints({"supplier": "DKV", "lines": []})
    assert "Capture accuracy for" in line
    assert "DKV" in line
    assert "line.vat" in weak
    assert "line.net" not in weak
    assert "often mis-read" in weak["line.vat"]


def test_review_screen_no_data_no_hints(cc):
    import app
    line, weak = app._capture_accuracy_hints({"supplier": "FRESH", "lines": []})
    assert line == ""
    assert weak == {}


def test_review_screen_escapes_xss_in_supplier_and_field(cc, monkeypatch):
    """A planted XSS in a supplier/field name must be escaped on the review surface."""
    import app
    monkeypatch.setattr(app, "_log_exc", lambda *a, **k: None)
    sup = '<script>alert(1)</script>'
    fld = '<img src=x onerror=alert(2)>'
    for _ in range(3):
        cc.record_outcome(sup, fld, was_correct=False)
    line, weak = app._capture_accuracy_hints({"supplier": sup, "lines": []})
    # the supplier name is escaped in the accuracy line
    assert "<script>" not in line
    assert "&lt;script&gt;" in line
    # the field name is escaped in the weak-field hint
    hint = weak.get(fld, "")
    assert "<img" not in hint
    assert "&lt;img" in hint


# ---------------------------------------------------------------- advisory / no-gate guard
def test_advisory_no_figure_or_gate_mutation(cc, monkeypatch):
    """STRUCTURAL guard: the module exposes NO mutator of a VAT figure / status / lock /
    legal gate — only the accuracy ledger functions. Recording an outcome changes nothing
    but its own DB."""
    import capture_confidence as M
    public = [n for n in dir(M) if not n.startswith("_")]
    # the only state-changing public function is record_outcome (telemetry into its own DB)
    forbidden = {"lock", "unlock", "set_status", "withdraw", "build_workbook",
                 "register", "apply", "commit", "gate", "validate"}
    assert not (set(public) & forbidden)

    # apply_corrections (the AI path) is the only place that edits a draft — and it returns
    # a COPY; recording capture outcomes must not change the verdict/correction result.
    import ai_verify
    monkeypatch.setattr(ai_verify, "capture_confidence", cc, raising=False)
    draft = {"supplier": "DKV", "capture": {"lines": [{"vat": 1.0}]},
             "lines": [{"vat": 1.0}]}
    verdict = {"fields": [{"name": "line[1].vat", "document": "2.0", "match": False}]}
    corrected, corrections = ai_verify.apply_corrections(draft, verdict)
    # the correction itself is unaffected by the telemetry; the input draft is untouched
    assert corrected["capture"]["lines"][0]["vat"] == 2.0
    assert draft["capture"]["lines"][0]["vat"] == 1.0
    assert len(corrections) == 1
